"""Own the disposable, retained-on-disk M1-01 verification environment.

Never reads dotenv, starts the normal stack, deletes data, or calls a model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "backend/src"))
from productflow_backend.infrastructure.verification import IsolationError, inside_run  # noqa: E402

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def binary_root() -> Path:
    candidates = [WORKSPACE, *WORKSPACE.parents]
    for candidate in candidates:
        if (candidate / ".local/postgresql/pgsql/bin/initdb.exe").is_file():
            return candidate / ".local"
    raise IsolationError("native_verification_binaries_unavailable")


def load_or_plan(run_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{3,40}", run_id):
        raise IsolationError("invalid_run_id")
    root = (WORKSPACE / ".local/verification/m1-01" / run_id).resolve()
    path = root / "manifest.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if Path(data["run_root"]).resolve() != root or data["workspace"] != str(
            WORKSPACE
        ):
            raise IsolationError("manifest_workspace_mismatch")
        return data
    role = "m101_" + run_id.replace("-", "_")
    return {
        "run_id": run_id,
        "run_root": str(root),
        "workspace": str(WORKSPACE),
        "environment_id": f"m1-01:{run_id}:{uuid4()}",
        "protocol_version": 1,
        "stub_only": True,
        "ports": {
            "postgres": 25432,
            "redis": 26379,
            "api": 29292,
            "web": 29293,
            "stub": 29294,
        },
        "database_role": role,
        "databases": [role, role + "_migration", role + "_fresh", role + "_fresh_final"],
        "cookie_name": f"m101_{run_id}_session",
        "queue": f"m101_{run_id}",
        "redis_namespace": f"productflow:m101:{run_id}",
        "processes": {},
        "system_identifier": None,
        "isolation_preflight_passed": False,
    }


def offline_preflight(data: dict, *, require_free: bool) -> None:
    root = Path(data["run_root"])
    if root != (WORKSPACE / ".local/verification/m1-01" / data["run_id"]).resolve():
        raise IsolationError("run_root_mismatch")
    if root.is_relative_to(binary_root() / "data"):
        raise IsolationError("run_overlaps_existing_data")
    for name in (
        "pgdata",
        "redis",
        "storage",
        "storage/spool",
        "logs",
        "evidence",
        "tmp",
        "cache",
    ):
        inside_run(root / name, data)
    if len(set(data["ports"].values())) != 5:
        raise IsolationError("verification_ports_not_unique")
    if set(data["ports"].values()) & {15432, 16379, 29280, 29281, 29282, 29283}:
        raise IsolationError("verification_uses_existing_environment_port")
    if require_free:
        for port in data["ports"].values():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))


def secret_values(data: dict) -> dict:
    return json.loads(
        (Path(data["run_root"]) / "private.json").read_text(encoding="utf-8")
    )


def pg_connect(data: dict, *, admin: bool = False, database: str | None = None):
    import psycopg

    values = secret_values(data)
    name = database or data["databases"][0]
    if name not in data["databases"] and name != "postgres":
        raise IsolationError("unregistered_database")
    return psycopg.connect(
        host="127.0.0.1",
        port=data["ports"]["postgres"],
        dbname=name,
        user="m101_cluster_admin" if admin else data["database_role"],
        password=values["pg_admin"] if admin else values["pg_app"],
        connect_timeout=3,
        autocommit=True,
    )


def redis_client(data: dict):
    import redis

    return redis.Redis(
        host="127.0.0.1",
        port=data["ports"]["redis"],
        db=0,
        password=secret_values(data)["redis"],
        socket_connect_timeout=3,
        decode_responses=True,
    )


def process_identity(pid: int) -> int:
    if os.name != "nt":
        return int(Path(f"/proc/{pid}/stat").read_text().split()[21])
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            raise ProcessLookupError(pid)
        raise IsolationError("cannot_verify_owned_process")
    try:
        creation, exit_time, kernel_time, user_time = (
            wintypes.FILETIME() for _ in range(4)
        )
        if not kernel.GetProcessTimes(
            handle,
            *(ctypes.byref(v) for v in (creation, exit_time, kernel_time, user_time)),
        ):
            raise IsolationError("cannot_verify_process_creation_time")
        if exit_time.dwHighDateTime or exit_time.dwLowDateTime:
            raise ProcessLookupError(pid)
        return creation.dwHighDateTime << 32 | creation.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def start_process(
    data: dict,
    name: str,
    command: list[str],
    env: dict | None = None,
    cwd: Path = WORKSPACE,
) -> None:
    root = Path(data["run_root"])
    if name in data["processes"]:
        record = data["processes"][name]
        try:
            if process_identity(record["pid"]) == record["creation_time"]:
                raise IsolationError("owned_process_already_running")
        except (OSError, IsolationError) as exc:
            if str(exc) == "owned_process_already_running":
                raise
    with (root / "logs" / f"{name}.log").open("ab") as log:
        if env and env.get("M101_MANIFEST") and command[0] == sys.executable:
            from productflow_backend.infrastructure.verification_process import (
                start_python,
            )

            process = start_python(command, env, stdout=log, cwd=cwd)
        else:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=CREATE_NO_WINDOW,
            )
    data["processes"][name] = {
        "pid": process.pid,
        "creation_time": process_identity(process.pid),
    }
    write_json(root / "manifest.json", data)


def wait_ready(check, seconds: int = 20) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise IsolationError("verification_service_did_not_become_ready_check_run_logs")


def start_postgres(data: dict) -> None:
    root = Path(data["run_root"])
    # pg_ctl drops the Windows administrator token for postgres; launching
    # postgres.exe directly from an elevated validation runner is rejected.
    subprocess.run(
        [
            str(binary_root() / "postgresql/pgsql/bin/pg_ctl.exe"),
            "start",
            "-w",
            "-t",
            "20",
            "-D",
            str(root / "pgdata"),
            "-l",
            str(root / "logs/postgres.log"),
        ],
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )
    pid = int((root / "pgdata/postmaster.pid").read_text().splitlines()[0])
    data["processes"]["postgres"] = {"pid": pid, "creation_time": process_identity(pid)}
    write_json(root / "manifest.json", data)


def prepare(data: dict) -> None:
    root = Path(data["run_root"])
    if (root / "private.json").exists():
        offline_preflight(data, require_free=False)
        record = data["processes"].get("postgres")
        if not record:
            raise IsolationError(
                "partial_prepare_requires_the_original_owned_postgres_process"
            )
        try:
            if process_identity(record["pid"]) != record["creation_time"]:
                raise IsolationError("partial_prepare_pid_reused")
        except ProcessLookupError:
            start_postgres(data)
        finish_prepare(data)
        return
    offline_preflight(data, require_free=True)
    for name in (
        "redis",
        "storage/spool",
        "storage/artifacts",
        "logs",
        "evidence",
        "tmp",
        "cache",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    values = {
        key: secrets.token_hex(24)
        for key in ("pg_admin", "pg_app", "redis", "session", "admin", "settings")
    }
    write_json(root / "private.json", values)
    write_json(root / "manifest.json", data)
    password_file = root / "pg-init-password"
    password_file.write_text(values["pg_admin"], encoding="ascii")
    pg = binary_root() / "postgresql/pgsql/bin"
    with (root / "logs/initdb.log").open("wb") as log:
        subprocess.run(
            [
                str(pg / "initdb.exe"),
                "-D",
                str(root / "pgdata"),
                "-U",
                "m101_cluster_admin",
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
                f"--pwfile={password_file}",
            ],
            stdout=log,
            stderr=log,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
    # The credential stays in private.json; no output contains it.
    password_file.unlink()
    with (root / "pgdata/postgresql.conf").open("a", encoding="utf-8") as config:
        config.write(
            f"\nlisten_addresses='127.0.0.1'\nport={data['ports']['postgres']}\n"
        )
    start_postgres(data)
    finish_prepare(data)


def finish_prepare(data: dict) -> None:
    root = Path(data["run_root"])
    values = secret_values(data)

    def pg_ready():
        with pg_connect(data, admin=True, database="postgres") as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    wait_ready(pg_ready)
    from psycopg import sql

    with pg_connect(data, admin=True, database="postgres") as conn:
        if (
            Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
            != root / "pgdata"
        ):
            raise IsolationError("partial_prepare_cluster_directory_mismatch")
        identity = str(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
        if data.get("system_identifier") and identity != data["system_identifier"]:
            raise IsolationError("postgres_cluster_identity_mismatch")
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (data["database_role"],)
        ).fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(
                    sql.Identifier(data["database_role"]), sql.Literal(values["pg_app"])
                )
            )
        for database in data["databases"]:
            existing = conn.execute(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",
                (database,),
            ).fetchone()
            if existing:
                if existing[0] != data["environment_id"]:
                    raise IsolationError("refusing_unmarked_existing_database")
                continue
            conn.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'"
                ).format(
                    sql.Identifier(database), sql.Identifier(data["database_role"])
                )
            )
            conn.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(database), sql.Literal(data["environment_id"])
                )
            )
        data["system_identifier"] = identity
    redis_config = root / "redis/redis.conf"
    redis_config.write_text(
        f"bind 127.0.0.1\nport {data['ports']['redis']}\nprotected-mode yes\n"
        f"requirepass {values['redis']}\ndir .\n"
        'appendonly yes\nsave ""\n',
        encoding="utf-8",
    )
    redis_binary = (
        binary_root() / "redis/Redis-8.10.3-Windows-x64-msys2/redis-server.exe"
    )
    record = data["processes"].get("redis")
    running = False
    if record:
        try:
            running = process_identity(record["pid"]) == record["creation_time"]
        except ProcessLookupError:
            pass
    if not running:
        start_process(
            data, "redis", [str(redis_binary), "redis.conf"], cwd=root / "redis"
        )
    wait_ready(lambda: redis_client(data).ping())
    redis_client(data).set("m101:environment_id", data["environment_id"], nx=True)
    write_json(root / "manifest.json", data)
    preflight(data)


def preflight(data: dict) -> None:
    offline_preflight(data, require_free=False)
    with pg_connect(data, admin=True, database="postgres") as conn:
        identity = str(
            conn.execute(
                "SELECT system_identifier FROM pg_control_system()"
            ).fetchone()[0]
        )
        data_dir = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
        if (
            identity != data["system_identifier"]
            or data_dir != Path(data["run_root"]) / "pgdata"
        ):
            raise IsolationError("postgres_cluster_identity_mismatch")
    for database in data["databases"]:
        with pg_connect(data, database=database) as conn:
            row = conn.execute(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
            )
            if row.fetchone()[0] != data["environment_id"]:
                raise IsolationError("postgres_database_marker_mismatch")
            if conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0]:
                raise IsolationError("application_role_must_not_be_superuser")
    client = redis_client(data)
    if client.get("m101:environment_id") != data["environment_id"]:
        raise IsolationError("redis_marker_mismatch")
    actual_dir = client.config_get("dir")["dir"]
    expected_dir = Path(data["run_root"]) / "redis"
    accepted_dirs = {
        expected_dir.as_posix(),
        "/" + expected_dir.relative_to(binary_root()).as_posix(),
    }
    if (
        actual_dir.replace("\\", "/") not in accepted_dirs
        or not (expected_dir / "appendonlydir").is_dir()
    ):
        raise IsolationError("redis_directory_mismatch")
    data["isolation_preflight_passed"] = True
    write_json(Path(data["run_root"]) / "manifest.json", data)
    write_json(
        Path(data["run_root"]) / "evidence/isolation.json",
        {
            key: data[key]
            for key in (
                "environment_id",
                "system_identifier",
                "databases",
                "ports",
                "stub_only",
            )
        },
    )
    print(
        "Dedicated PostgreSQL identity, restricted app role, Redis marker/directory and run paths verified."
    )


def child_environment(data: dict, mode: str = "integration") -> dict:
    values = secret_values(data)
    root = Path(data["run_root"])
    keep = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(
        {
            "M101_MANIFEST": str(root / "manifest.json"),
            "M101_PROCESS_MODE": mode,
            "PYTHONPATH": os.pathsep.join(
                str(WORKSPACE / p)
                for p in ("scripts/verification_bootstrap", "backend/src")
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "APP_ENV": "verification",
            "APP_HOST": "127.0.0.1",
            "PYTHONIOENCODING": "utf-8",
            "DATABASE_URL": f"postgresql+psycopg://{data['database_role']}:{quote(values['pg_app'])}"
            f"@127.0.0.1:{data['ports']['postgres']}/{data['databases'][0]}",
            "REDIS_URL": f"redis://:{quote(values['redis'])}@127.0.0.1:{data['ports']['redis']}/0",
            "STORAGE_ROOT": str(root / "storage"),
            "LOG_DIR": str(root / "logs"),
            "SESSION_SECRET": values["session"],
            "ADMIN_ACCESS_KEY": values["admin"],
            "SETTINGS_ACCESS_TOKEN": values["settings"],
            "SESSION_COOKIE_NAME": data["cookie_name"],
            "PROTOTYPE_EXECUTION_ENABLED": "true",
            "EXECUTION_STUB_ONLY": "true",
            "EXECUTION_ENVIRONMENT_ID": data["environment_id"],
            "EXECUTION_QUEUE_NAME": data["queue"],
            "EXECUTION_REDIS_NAMESPACE": data["redis_namespace"],
            "EXECUTION_RELEASE_ID": "m1-01-stub-v1",
            "TEXT_PROVIDER_KIND": "mock",
            "IMAGE_PROVIDER_KIND": "mock",
            "POSTER_GENERATION_MODE": "template",
            "ADMIN_ACCESS_REQUIRED": "true",
            "DELETION_ENABLED": "false",
            "TEMP": str(root / "tmp"),
            "TMP": str(root / "tmp"),
            "TMPDIR": str(root / "tmp"),
            "RUFF_CACHE_DIR": str(root / "cache/ruff"),
            "NO_PROXY": "127.0.0.1",
            "WEB_PORT": str(data["ports"]["web"]),
            "VITE_DEV_PROXY_TARGET": f"http://127.0.0.1:{data['ports']['api']}",
            "VITE_VERIFICATION_ENVIRONMENT_ID": data["environment_id"],
            "M101_WEB_CACHE": str(root / "cache/vite"),
            "M101_WEB_DIST": str(root / "web-dist"),
            "BACKEND_CORS_ORIGINS": f"http://127.0.0.1:{data['ports']['web']}",
        }
    )
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    if font.exists():
        env["POSTER_FONT_PATH"] = str(font)
    if mode == "unit":
        env["DATABASE_URL"] = (
            f"sqlite:///{root / 'tmp' / ('unit-default-' + uuid4().hex + '.db')}"
        )
        env["REDIS_URL"] = f"redis://127.0.0.1:{data['ports']['redis']}/0"
        env["PROTOTYPE_EXECUTION_ENABLED"] = "false"
    return env


def run_command(
    data: dict, command: list[str], *, mode: str = "integration", name: str = "command"
) -> int:
    preflight(data)
    if command[0] == "python":
        command[0] = sys.executable
    frontend = command[0] == "pnpm"
    if frontend:
        # Reuse the repository's installed pnpm 10 and Node 22 with the v10
        # offline store; do not upgrade tools or fall back to online resolution.
        node = binary_root() / "node-v22.23.5-win-x64/node.exe"
        pnpm = binary_root() / "npm/node_modules/pnpm/bin/pnpm.cjs"
        if not node.is_file() or not pnpm.is_file():
            raise IsolationError("repository_frontend_tools_unavailable")
        command = [str(node), str(pnpm), *command[1:]]
    if "pytest" in command:
        if mode == "integration":
            for worker_name in ("worker0", "worker1", "execution_scanner", "outbox_publisher"):
                record = data["processes"].get(worker_name)
                if record:
                    try:
                        identity = process_identity(record["pid"])
                    except ProcessLookupError:
                        continue
                    if identity == record["creation_time"]:
                        raise IsolationError("pause_browser_delivery_before_integration_tests")
        suffix = str(uuid4())[:8]
        command += [
            f"--basetemp={Path(data['run_root']) / 'tmp' / ('pytest-' + suffix)}",
            "-o",
            f"cache_dir={Path(data['run_root']) / 'cache/pytest'}",
        ]
    started = time.time()
    cwd = WORKSPACE
    if "alembic" in command:
        cwd = WORKSPACE / "backend"
        command = [
            "alembic.ini" if part == "backend/alembic.ini" else part for part in command
        ]
    env = web_environment(data) if frontend else child_environment(data, mode)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    for secret in secret_values(data).values():
        output = output.replace(secret, "[redacted]")
    output_name = f"{name}-{int(started)}-{uuid4().hex[:6]}.log"
    (Path(data["run_root"]) / "evidence" / output_name).write_text(
        output, encoding="utf-8"
    )
    print(output[-20000:])
    record = {
        "command": command,
        "exit_code": result.returncode,
        "started_at_unix": started,
        "elapsed_seconds": time.time() - started,
        "mode": mode,
        "output_log": output_name,
    }
    with (Path(data["run_root"]) / "evidence/commands.jsonl").open(
        "a", encoding="utf-8"
    ) as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result.returncode


def web_environment(data: dict) -> dict:
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "M101_MANIFEST",
        "WEB_PORT",
        "VITE_DEV_PROXY_TARGET",
        "VITE_VERIFICATION_ENVIRONMENT_ID",
        "M101_WEB_CACHE",
        "M101_WEB_DIST",
        "NO_PROXY",
    }
    env = {
        key: value
        for key, value in child_environment(data).items()
        if key.upper() in allowed
    }
    env["PATH"] = (
        str(binary_root() / "node-v22.23.5-win-x64") + os.pathsep + env.get("PATH", "")
    )
    return env


def prepare_web(data: dict) -> int:
    preflight(data)
    source = binary_root() / "cache/pnpm"
    target = Path(data["run_root"]) / "cache/pnpm-store"
    if not target.exists():
        shutil.copytree(source, target)
    return run_command(
        data,
        [
            "pnpm",
            "--dir",
            "web",
            "install",
            "--offline",
            "--frozen-lockfile",
            "--ignore-scripts",
            "--store-dir",
            str(target),
        ],
    )


def stop(data: dict) -> None:
    for name, record in reversed(list(data["processes"].items())):
        try:
            if process_identity(record["pid"]) != record["creation_time"]:
                raise IsolationError("pid_reused_refusing_to_stop")
        except OSError:
            continue
        if name == "postgres":
            subprocess.run(
                [
                    str(binary_root() / "postgresql/pgsql/bin/pg_ctl.exe"),
                    "stop",
                    "-m",
                    "fast",
                    "-D",
                    str(Path(data["run_root"]) / "pgdata"),
                ],
                check=True,
                creationflags=CREATE_NO_WINDOW,
            )
        elif name == "redis":
            redis_client(data).shutdown(save=False)
        else:
            import signal

            os.kill(record["pid"], signal.SIGTERM)
    print(
        "Stopped owned verification processes; database, artifacts and evidence retained."
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "print-plan",
            "prepare",
            "prepare-web",
            "add-fixture",
            "preflight",
            "migrate",
            "seed",
            "run",
            "start-app",
            "start-web",
            "pause-delivery",
            "resume-delivery",
            "stop",
        ],
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture-suffix")
    parser.add_argument("--stage", choices=["expand", "data", "head"], default="data")
    parser.add_argument(
        "--mode", choices=["unit", "integration"], default="integration"
    )
    args, remainder = parser.parse_known_args()
    data = load_or_plan(args.run_id)
    if args.action == "print-plan":
        offline_preflight(data, require_free=True)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.action == "prepare":
        prepare(data)
    elif args.action == "prepare-web":
        return prepare_web(data)
    elif args.action == "add-fixture":
        preflight(data)
        suffix = args.fixture_suffix or ""
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,24}", suffix):
            raise IsolationError("invalid_fixture_suffix")
        from psycopg import sql

        database = data["database_role"] + "_" + suffix
        if database in data["databases"]:
            raise IsolationError("fixture_already_registered_never_reset")
        with pg_connect(data, admin=True, database="postgres") as conn:
            conn.autocommit = True
            if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
                raise IsolationError("unregistered_database_never_adopted")
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database), sql.Identifier(data["database_role"])
            ))
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database), sql.Literal(data["environment_id"])
            ))
        data["databases"].append(database)
        write_json(Path(data["run_root"]) / "manifest.json", data)
        preflight(data)
        print("Registered one new empty fixture database; existing databases preserved.")
    elif args.action == "preflight":
        preflight(data)
    elif args.action == "migrate":
        revision = "20261123_0030" if args.stage == "expand" else "head"
        return run_command(
            data,
            [
                "python",
                "-m",
                "alembic",
                "-c",
                "backend/alembic.ini",
                "upgrade",
                revision,
            ],
        )
    elif args.action == "seed":
        return run_command(
            data, ["python", "-m", "productflow_backend.verification_seed"]
        )
    elif args.action == "run":
        command = remainder[1:] if remainder and remainder[0] == "--" else remainder
        if not command:
            parser.error("run requires -- <command>")
        return run_command(data, command, mode=args.mode)
    elif args.action == "start-web":
        preflight(data)
        import httpx

        with httpx.Client(trust_env=False, timeout=3) as client:
            identity = client.get(
                f"http://127.0.0.1:{data['ports']['api']}/api/verification-context"
            ).json()
        if (
            identity.get("environment_id") != data["environment_id"]
            or identity.get("stub_only") is not True
        ):
            raise IsolationError("proxy_target_identity_mismatch")
        start_process(
            data,
            "web",
            [
                str(binary_root() / "node-v22.23.5-win-x64/node.exe"),
                str(WORKSPACE / "web/node_modules/vite/bin/vite.js"),
            ],
            web_environment(data),
            WORKSPACE / "web",
        )
    elif args.action == "start-app":
        preflight(data)
        env = child_environment(data)
        start_process(
            data,
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "productflow_backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(data["ports"]["api"]),
            ],
            env,
        )
        for name in ("outbox_publisher", "execution_scanner"):
            start_process(
                data, name, [sys.executable, "-m", "productflow_backend." + name], env
            )
        for index in range(2):
            start_process(
                data,
                f"worker{index}",
                [sys.executable, "-m", "productflow_backend.verification_worker"],
                env,
            )
    elif args.action == "pause-delivery":
        preflight(data)
        import signal

        for name in ("worker0", "worker1", "execution_scanner", "outbox_publisher"):
            record = data["processes"].get(name)
            if not record:
                continue
            try:
                if process_identity(record["pid"]) != record["creation_time"]:
                    raise IsolationError("pid_reused_refusing_to_stop")
                os.kill(record["pid"], signal.SIGTERM)
            except ProcessLookupError:
                continue
        print("Paused owned delivery processes; retained all messages, records and quotas.")
    elif args.action == "resume-delivery":
        preflight(data)
        env = child_environment(data)
        for name in ("outbox_publisher", "execution_scanner", "worker0", "worker1"):
            module = "verification_worker" if name.startswith("worker") else name
            start_process(data, name, [sys.executable, "-m", "productflow_backend." + module], env)
    else:
        stop(data)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IsolationError as error:
        print(f"Isolation refused: {error}", file=sys.stderr)
        raise SystemExit(78) from None
