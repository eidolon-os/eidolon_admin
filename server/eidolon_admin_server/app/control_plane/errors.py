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
    #: The authority answers, but the specific Realm/instance the request needs
    #: is not running. Distinct from ``unavailable`` because the next action
    #: differs: nothing is wrong with Memory, this one space has to be brought
    #: up. Reporting it as ``unavailable`` sent people to look at the wrong
    #: service.
    "runtime_missing",
    "upstream_failure",
    "contract_violation",
    "configuration",
]


@dataclass(slots=True)
class AuthorityFailure(Exception):
    authority: Literal["directory", "data", "hub", "kernel", "memory"]
    kind: FailureKind
    detail: str
    status_code: int
    upstream_status: int | None = None
    retryable: bool = False
    #: The authority's own word for *which* refusal this is, when it gave one.
    #: ``kind`` says how to treat the failure at the transport layer; this says
    #: what happened in the domain, and the two are not the same question. A
    #: Companion that cannot be archived because it is the one that answers is a
    #: ``conflict`` — so is a lost race — and only one of them is a question a
    #: person can answer. Carried rather than parsed out of the sentence, because
    #: matching on English across two process boundaries is not a contract.
    code: str | None = None

    def __str__(self) -> str:
        return self.detail

    def to_wire(self) -> WorkflowFailure:
        return WorkflowFailure(
            authority=self.authority,
            kind=self.kind,
            detail=self.detail,
            code=self.code,
            upstream_status=self.upstream_status,
            retryable=self.retryable,
        )
