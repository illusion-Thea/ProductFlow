"""Negative checks stop at guards, before any forbidden connection or write."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from sqlalchemy import create_engine

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.verification import (
    IsolationError,
    check_endpoint,
    inside_run,
    manifest,
)


def main() -> None:
    data = manifest()
    settings = get_settings()
    assert settings.execution_environment_id == data["environment_id"]
    checks = {
        "old_postgres": lambda: check_endpoint(
            "postgresql://x:x@127.0.0.1:15432/productflow", "database"
        ),
        "old_redis": lambda: check_endpoint("redis://127.0.0.1:16379/1", "redis"),
        "old_storage": lambda: inside_run(
            Path(data["workspace"]).parents[2] / "backend/storage-dev"
        ),
        "external_provider": lambda: socket.create_connection(("api.openai.com", 443)),
        "old_api": lambda: socket.create_connection(("127.0.0.1", 29282)),
        "old_postgres_driver": lambda: create_engine(
            "postgresql+psycopg://x:x@127.0.0.1:15432/productflow"
        ).connect(),
        "external_image_download": lambda: socket.create_connection(
            ("example.org", 443)
        ),
    }
    passed = []
    for name, check in checks.items():
        try:
            check()
        except IsolationError:
            passed.append(name)
        else:
            raise AssertionError(f"isolation guard did not reject {name}")
    evidence = {
        "environment_id": data["environment_id"],
        "blocked_before_connect_or_write": passed,
        "resolved_module": str(
            Path(
                __import__("productflow_backend.config", fromlist=["__file__"]).__file__
            )
        ),
        "external_requests": 0,
        "cookie_name": settings.session_cookie_name,
    }
    (Path(data["run_root"]) / "evidence/network-guard.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(f"Isolation negative checks passed: {len(passed)}; external requests: 0")


if __name__ == "__main__":
    main()
