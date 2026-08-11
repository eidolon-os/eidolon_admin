"""Failures Admin reports when the Host system manager cannot serve a request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HostServiceFailureKind = Literal[
    "unavailable", "not_found", "conflict", "rejected", "invalid_response"
]

_STATUS: dict[HostServiceFailureKind, int] = {
    "unavailable": 503,
    "not_found": 404,
    "conflict": 409,
    "rejected": 502,
    "invalid_response": 502,
}


@dataclass(frozen=True, slots=True)
class HostServiceError(Exception):
    kind: HostServiceFailureKind
    detail: str

    def __str__(self) -> str:
        return self.detail

    @property
    def status_code(self) -> int:
        return _STATUS[self.kind]
