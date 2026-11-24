"""Review regressions: never make a connection to test a forbidden destination."""

import importlib
import sys
from types import ModuleType

import psycopg
import pytest
from sqlalchemy import event

from productflow_backend import config
from productflow_backend.infrastructure import verification


def test_runtime_override_never_reenters_dotenv(tmp_path, monkeypatch):
    poison = tmp_path / ".env"
    poison.write_text("TEXT_API_KEY=poison-must-not-load\n", encoding="utf-8")
    monkeypatch.setitem(config.Settings.model_config, "env_file", str(poison))
    monkeypatch.delenv("TEXT_API_KEY", raising=False)
    monkeypatch.setattr(config, "_load_database_config_overrides", lambda: {"image_session_prompt_max_length": "2000"})
    settings = config.get_runtime_settings()
    assert settings.text_api_key != "poison-must-not-load"
    assert settings.image_session_prompt_max_length == 2000
    verification.validate_settings(settings)


@pytest.mark.parametrize("route", ["kwargs", "conninfo", "sqlalchemy", "environment"])
def test_native_hostaddr_cannot_redirect_registered_host(monkeypatch, route):
    calls, listeners = [], []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(event, "listens_for", lambda *a, **k: lambda fn: listeners.append(fn) or fn)
    verification.install_database_guard()
    data = verification.manifest()
    values = dict(
        host="127.0.0.1", port=data["ports"]["postgres"], dbname=data["databases"][0], user=data["database_role"]
    )
    if route == "environment":
        monkeypatch.setenv("PGHOSTADDR", "203.0.113.7")
    else:
        values["hostaddr"] = "203.0.113.7"
    with pytest.raises(verification.IsolationError):
        if route == "sqlalchemy":
            listeners[0](type("Dialect", (), {"name": "postgresql"})(), None, [], values)
        elif route == "conninfo":
            psycopg.connect(" ".join(f"{k}={v}" for k, v in values.items()))
        else:
            psycopg.connect(**values)
    assert calls == []


@pytest.mark.parametrize(
    ("entrypoint", "module", "function"),
    [
        ("execution_scanner", "application.execution.recovery", "scan_deadlines"),
        ("outbox_publisher", "infrastructure.outbox", "publish_one"),
    ],
)
def test_background_entrypoint_without_manifest_stops_before_business_import(monkeypatch, entrypoint, module, function):
    monkeypatch.delenv("M101_MANIFEST", raising=False)
    calls = []
    fake = ModuleType(f"productflow_backend.{module}")

    def business_access():
        calls.append(function)
        raise AssertionError("business code reached without isolation")

    setattr(fake, function, business_access)
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    name = f"productflow_backend.{entrypoint}"
    monkeypatch.delitem(sys.modules, name, raising=False)
    entry = importlib.import_module(name)
    with pytest.raises(verification.IsolationError, match="manifest"):
        entry.main()
    assert not calls
