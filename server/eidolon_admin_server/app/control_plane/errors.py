"""Stable failure vocabulary for remote bounded-context calls.

Deliberately *not* in the SDK, unlike the refusal envelope this eventually
projects onto. ``Authority`` is this process's view of its own upstreams — Data
does not know it is called "data" — so publishing it as a shared contract would
make one service's topology naming a dependency of the services it names, and
would put a value in a cross-repo package that only one repo can ever be right
about. The words a *client* acts on are shared; the words this process uses
about who refused it are its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from eidolon_sdk.biz.contracts.refusal import Refusal

from .contracts import PUBLIC_REFUSAL_KIND, Authority, FailureKind, WorkflowFailure

#: Re-exported so the ~45 raise sites keep importing their vocabulary from the
#: module that owns raising, while the values are declared once next to the
#: model that serialises them. Two independent spellings of one tuple of strings
#: is precisely what let ``agent`` exist in the raising code and not in the wire
#: model — and a refusal that cannot be serialised is a 500.
__all__ = ["Authority", "AuthorityFailure", "FailureKind"]


@dataclass(slots=True)
class AuthorityFailure(Exception):
    """An upstream bounded context refused, or could not answer.

    Raised deep in the clients and converted to HTTP in exactly one place — the
    handler registered on the app. Nothing between those two points catches it,
    which is what keeps "how a refusal becomes a response" from being answered
    thirty times.
    """

    authority: Authority
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
        """The same refusal, in the shape the internal ABI publishes.

        Cannot reject a value this exception accepts: both read their vocabulary
        from ``contracts``. That is the entire point of the shared declaration —
        this runs inside an exception handler, where a raise replaces a chosen
        status with an unexplained 500 and loses the reason with it.

        Carries the client-facing projection alongside the operator-facing
        facts, because this is the last place that knows both. The boundary that
        publishes to a phone is a different process which imports nothing from
        here, so if it had to derive ``kind`` itself it would be deriving it
        from a vocabulary it cannot see change.
        """

        return WorkflowFailure(
            authority=self.authority,
            kind=self.kind,
            detail=self.detail,
            code=self.code,
            upstream_status=self.upstream_status,
            retryable=self.retryable,
            refusal=self.to_refusal(),
        )

    def to_refusal(self) -> Refusal:
        """This refusal in the words a client acts on.

        ``reason`` is this process's own sentence, relayed rather than rewritten.
        A client prefers its own wording for a kind it knows and falls back to
        this — which is what keeps the long tail of refusals readable without
        every one of them needing a screen.
        """

        return Refusal(
            kind=PUBLIC_REFUSAL_KIND[self.kind],
            reason=self.detail,
            code=self.code,
            retryable=self.retryable,
        )
