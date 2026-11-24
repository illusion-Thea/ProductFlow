from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class DispatchPermit:
    call_id: str
    stage_id: str
    attempt_id: str
    claim_token: str
    claim_epoch: int
    snapshot: dict[str, Any]
    modality: str


@dataclass(frozen=True)
class TransportResult:
    request_id: str
    payload: bytes
    mime_type: str
    usage: dict[str, Any] = field(default_factory=dict)


class SingleRequestTransport(Protocol):
    def send(self, permit: DispatchPermit) -> TransportResult: ...


class ReadOnlyRequestLookup(Protocol):
    def retrieve(self, request_id: str) -> TransportResult | None: ...
