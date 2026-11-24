import socket

import psycopg
import pytest

from productflow_backend.config import Settings, get_settings
from productflow_backend.infrastructure.provider_transport import single_request_http_client
from productflow_backend.infrastructure.verification import IsolationError, validate_settings


def test_root_dotenv_poison_is_ignored_and_cookie_identity_is_required(tmp_path, monkeypatch):
    poison = tmp_path / ".env"
    poison.write_text(
        "DATABASE_URL=postgresql://poison@127.0.0.1:15432/productflow\n"
        "TEXT_API_KEY=poison-must-not-load\nSESSION_COOKIE_NAME=session\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(Settings.model_config, "env_file", str(poison))
    monkeypatch.delenv("TEXT_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert "15432" not in settings.database_url and settings.text_api_key != "poison-must-not-load"
        with pytest.raises(IsolationError, match="cookie"):
            validate_settings(settings.model_copy(update={"session_cookie_name": "session"}))
        with pytest.raises(IsolationError, match="environment"):
            validate_settings(settings.model_copy(update={"execution_environment_id": "other"}))
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "action",
    [
        lambda: single_request_http_client(),
        lambda: socket.create_connection(("api.openai.com", 443)),
        lambda: socket.create_connection(("127.0.0.1", 29282)),
        lambda: psycopg.connect("host=127.0.0.1 port=15432 dbname=productflow user=poison"),
    ],
)
def test_real_sdk_external_download_old_api_and_native_driver_blocked_before_connection(action):
    with pytest.raises(IsolationError):
        action()
