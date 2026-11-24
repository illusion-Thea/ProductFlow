import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from productflow_backend.infrastructure.background_loop import run_polling_loop


def test_transient_poll_failure_retries_without_logging_parameters(monkeypatch, caplog):
    calls, waits = [], []
    monkeypatch.setattr("productflow_backend.infrastructure.background_loop.time.sleep", waits.append)

    def step():
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("SECRET SQL", {"password": "secret-value"}, RuntimeError("disconnected"))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_polling_loop(step, 0.2)
    assert len(calls) == 2 and waits == [0.5]
    assert "secret" not in caplog.text.lower()


def test_programming_error_is_not_hidden():
    def step():
        raise ProgrammingError("bad SQL", {}, RuntimeError("programming fault"))

    with pytest.raises(ProgrammingError):
        run_polling_loop(step, 0.2)
