"""Forgetting something, in the two steps the realm defines.

Thin by necessity rather than by taste: the realm owns the resolution, the
signed binding between the two steps, and the mutation. What this layer must not
do is interpret any of it —

- it does not re-resolve on confirm. The token names the exact set; re-resolving
  would act on whatever the words match now, which is not what the person saw.
- it does not read the token. It is signed by the realm that minted it, and a
  layer able to interpret one is a layer able to build one.
- it does not turn the realm's ``status`` into success or failure. "Nothing
  matched" and "too many matched" lead to different next steps, and collapsing
  them would leave a client unable to tell a person which happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import (
    OwnerForgetOutcome,
    OwnerForgetPreview,
    OwnerForgetProgress,
)


@runtime_checkable
class MemoryForgetter(Protocol):
    """The three authority calls these steps need."""

    async def forget_preview(
        self,
        *,
        owner_id: str,
        target: str,
    ) -> OwnerForgetPreview: ...

    async def forget_confirm(
        self,
        *,
        owner_id: str,
        confirmation_token: str,
    ) -> OwnerForgetOutcome: ...

    async def forget_status(
        self,
        *,
        owner_id: str,
        request_id: str,
    ) -> OwnerForgetProgress: ...


@dataclass(frozen=True, slots=True)
class ForgetEntryView:
    entry_id: str
    preview: str
    #: How sure the match is. Carried because a client shows an uncertain match
    #: differently from an exact one, and because "needs_confirmation" alone
    #: cannot say *which* entry was the doubtful one.
    score: float


@dataclass(frozen=True, slots=True)
class ForgetProposal:
    status: str
    target: str
    entries: tuple[ForgetEntryView, ...]
    needs_confirmation: bool
    confirmation_token: str | None
    expires_at: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ForgetResult:
    #: Names this change for :func:`read_forget_progress`. A second confirm of
    #: the same preview names the same change.
    request_id: str
    target: str
    entry_count: int
    status: str


@dataclass(frozen=True, slots=True)
class ForgetProgress:
    request_id: str
    status: str


async def propose_forget(
    *,
    owner_id: str,
    target: str,
    memory: MemoryForgetter,
) -> ForgetProposal:
    preview = await memory.forget_preview(owner_id=owner_id, target=target)
    return ForgetProposal(
        status=preview.status,
        target=preview.target,
        entries=tuple(
            ForgetEntryView(
                entry_id=entry.entry_id,
                preview=entry.preview,
                score=entry.score,
            )
            for entry in preview.entries
        ),
        needs_confirmation=preview.needs_confirmation,
        confirmation_token=preview.confirmation_token,
        expires_at=preview.expires_at,
        detail=preview.detail,
    )


async def apply_forget(
    *,
    owner_id: str,
    confirmation_token: str,
    memory: MemoryForgetter,
) -> ForgetResult:
    outcome = await memory.forget_confirm(
        owner_id=owner_id, confirmation_token=confirmation_token
    )
    return ForgetResult(
        request_id=outcome.request_id,
        target=outcome.target,
        entry_count=outcome.entry_count,
        status=outcome.status,
    )


async def read_forget_progress(
    *,
    owner_id: str,
    request_id: str,
    memory: MemoryForgetter,
) -> ForgetProgress:
    """Where a confirmed forget has got to.

    The confirm usually answers ``accepted`` — applying runs on the realm's
    worker — so without this a client could only say 「正在生效」 forever.
    """

    progress = await memory.forget_status(owner_id=owner_id, request_id=request_id)
    return ForgetProgress(request_id=progress.request_id, status=progress.status)
