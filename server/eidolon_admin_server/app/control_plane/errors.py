"""Stable failure vocabulary for remote bounded-context calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts import WorkflowFailure

FailureKind = Literal[
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "invalid_request",
    "unavailable",
    "upstream_failure",
    "contract_violation",
    "configuration",
]


@dataclass(slots=True)
class AuthorityFailure(Exception):
    authority: Literal["directory", "data", "hub", "kernel"]
    kind: FailureKind
    detail: str
    status_code: int
    upstream_status: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.detail

    def to_wire(self) -> WorkflowFailure:
        return WorkflowFailure(
            authority=self.authority,
            kind=self.kind,
            detail=self.detail,
            upstream_status=self.upstream_status,
            retryable=self.retryable,
        )
