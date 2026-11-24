"""Explicit transport policy shared by SDK adapters; retrieval never generates."""

import httpx

from productflow_backend.config import get_settings


def require_real_transport_disabled_in_verification() -> None:
    if get_settings().prototype_execution_enabled:
        from productflow_backend.infrastructure.verification import IsolationError

        raise IsolationError("real_sdk_disabled_in_stub_execution")


def single_request_http_client() -> httpx.Client:
    require_real_transport_disabled_in_verification()
    return httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
