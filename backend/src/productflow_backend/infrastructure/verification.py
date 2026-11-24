"""Fail-closed guard for the explicitly isolated M1-01 validation processes.

No application imports: this module runs from sitecustomize before SDK/DB imports.
The guard complements dedicated infrastructure; it is not a production sandbox.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class IsolationError(RuntimeError):
    pass


def manifest() -> dict[str, Any]:
    path = os.environ.get("M101_MANIFEST")
    if not path:
        raise IsolationError("verification_manifest_required")
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if result.get("protocol_version") != 1 or result.get("stub_only") is not True:
        raise IsolationError("verification_protocol_or_stub_guard_invalid")
    root = Path(result["run_root"]).resolve()
    if Path(path).resolve() != root / "manifest.json" or root.name != result["run_id"]:
        raise IsolationError("verification_manifest_root_mismatch")
    if result["run_id"] not in result["environment_id"] or root.parent.name != "m1-01":
        raise IsolationError("verification_identity_invalid")
    return result


def inside_run(path: str | Path, data: dict[str, Any] | None = None) -> Path:
    data = data or manifest()
    root = Path(data["run_root"]).resolve()
    target = Path(path).resolve()
    if target == root or not target.is_relative_to(root):
        raise IsolationError("path_outside_verification_run")
    # resolve() traverses existing symlinks/junctions before the containment check.
    return target


def check_endpoint(url: str, kind: str, data: dict[str, Any] | None = None) -> None:
    data = data or manifest()
    parsed = urlsplit(url.replace("postgresql+psycopg:", "postgresql:"))
    if kind == "database" and parsed.scheme == "sqlite":
        if os.environ.get("M101_PROCESS_MODE") != "unit":
            raise IsolationError("sqlite_only_in_unit_mode")
        if parsed.path not in {"", "/:memory:"}:
            inside_run(parsed.path.lstrip("/") if os.name == "nt" else parsed.path, data)
        return
    expected_port = data["ports"]["postgres" if kind == "database" else "redis"]
    if parsed.hostname != "127.0.0.1" or parsed.port != expected_port:
        raise IsolationError("endpoint_outside_verification_run")
    if kind == "database":
        if parsed.path.lstrip("/") not in data["databases"] or parsed.username != data["database_role"]:
            raise IsolationError("database_identity_not_registered")
    elif parsed.path != "/0":
        raise IsolationError("redis_database_mismatch")


def validate_settings(settings: Any) -> None:
    data = manifest()
    check_endpoint(settings.database_url, "database", data)
    check_endpoint(settings.redis_url, "redis", data)
    inside_run(settings.storage_root, data)
    inside_run(settings.log_dir, data)
    if (
        settings.session_cookie_name != data["cookie_name"]
        or settings.execution_environment_id != data["environment_id"]
    ):
        raise IsolationError("cookie_or_execution_environment_mismatch")
    unit_fixture = os.environ.get("M101_PROCESS_MODE") == "unit" and not settings.prototype_execution_enabled
    if not settings.execution_stub_only or (settings.app_env != "verification" and not unit_fixture):
        raise IsolationError("stub_only_verification_required")


def install_network_guard() -> None:
    data = manifest()
    if getattr(socket, "_m101_guard_installed", False):
        return
    allowed_ports = set(data["ports"].values())
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def allowed(address: Any) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            raise IsolationError("non_tcp_transport_not_allowed")
        host, port = str(address[0]), int(address[1])
        if os.environ.get("M101_PROCESS_MODE") == "unit":
            raise IsolationError("unit_tests_cannot_open_network_connections")
        if host != "127.0.0.1" or port not in allowed_ports:
            raise IsolationError("network_destination_not_in_verification_manifest")

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        allowed(address)
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
        allowed(address)
        return original_connect_ex(sock, address)

    def guarded_resolve(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        # Also block DNS lookups, not just the eventual TCP connection.
        if host not in {None, "127.0.0.1", b"127.0.0.1", "localhost"}:
            raise IsolationError("external_dns_not_allowed")
        return original_getaddrinfo(host, port, *args, **kwargs)

    def internal_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        # Windows asyncio implements its self-pipe with loopback TCP. Bind our
        # own ephemeral listener and bypass the guard for this single internal
        # connection only; arbitrary loopback ports remain forbidden.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                original_connect(client, listener.getsockname())
                server, _ = listener.accept()
            except BaseException:
                client.close()
                raise
        return server, client

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_resolve
    socket.socketpair = internal_socketpair
    socket._m101_guard_installed = True


def install_database_guard() -> None:
    # libpq performs sockets in C, so Python's socket guard is insufficient.
    import psycopg
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    original_connect = psycopg.connect

    def guarded_psycopg_connect(conninfo: str = "", **kwargs: Any) -> Any:
        from psycopg.conninfo import conninfo_to_dict

        values = conninfo_to_dict(conninfo, **kwargs)
        data = manifest()
        if (
            os.environ.get("M101_PROCESS_MODE") == "unit"
            or values.get("host") != "127.0.0.1"
            or int(values.get("port", 0)) != data["ports"]["postgres"]
            or values.get("dbname") not in data["databases"]
            or values.get("user") != data["database_role"]
        ):
            raise IsolationError("native_database_connection_not_registered")
        return original_connect(conninfo, **kwargs)

    psycopg.connect = guarded_psycopg_connect

    @event.listens_for(Engine, "do_connect")
    def validate_connection(dialect: Any, connection_record: Any, args: Any, kwargs: Any) -> None:
        if dialect.name == "sqlite":
            path = args[0] if args else kwargs.get("database", "")
            if os.environ.get("M101_PROCESS_MODE") != "unit":
                raise IsolationError("sqlite_only_in_unit_mode")
            if path not in {"", ":memory:"}:
                inside_run(path)
            return
        data = manifest()
        if dialect.name != "postgresql" or kwargs.get("host") != "127.0.0.1":
            raise IsolationError("database_driver_or_host_not_allowed")
        if int(kwargs.get("port", 0)) != data["ports"]["postgres"]:
            raise IsolationError("database_port_not_allowed")
        if kwargs.get("dbname") not in data["databases"] or kwargs.get("user") != data["database_role"]:
            raise IsolationError("database_identity_not_registered")
        if os.environ.get("M101_PROCESS_MODE") == "unit":
            raise IsolationError("unit_tests_cannot_connect_postgres")
