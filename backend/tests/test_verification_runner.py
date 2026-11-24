import importlib.util
from pathlib import Path


def test_prepare_restarts_stopped_preflighted_run_without_reinitializing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "verify_runner", Path(__file__).resolve().parents[2] / "scripts/verify_m101.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    (tmp_path / "private.json").write_text("{}")
    events = []
    data = {
        "run_root": str(tmp_path),
        "isolation_preflight_passed": True,
        "processes": {"postgres": {"pid": 123, "creation_time": 456}},
    }
    monkeypatch.setattr(runner, "offline_preflight", lambda *_a, **_k: events.append("offline"))
    monkeypatch.setattr(runner, "process_identity", lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(runner, "start_postgres", lambda *_: events.append("restart-owned"))
    monkeypatch.setattr(runner, "finish_prepare", lambda *_: events.append("verify-existing-data"))

    def online(_):
        assert "restart-owned" in events, "online check cannot succeed before restarting stopped infrastructure"

    monkeypatch.setattr(runner, "preflight", online)
    runner.prepare(data)
    assert events == ["offline", "restart-owned", "verify-existing-data"]
