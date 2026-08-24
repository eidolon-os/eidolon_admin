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
    ForgetOutcome,
    ForgetPreview,
)


@runtime_checkable
class MemoryForgetter(Protocol):
    """The two authority calls these steps need."""

    async def forget_preview(
        self,
        *,
        owner_id: str,
        target: str,
        action: str = "delete",
    ) -> ForgetPreview: ...

    async def forget_confirm(
        self,
        *,
        owner_id: str,
        confirmation_token: str,
    ) -> ForgetOutcome: ...


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
    action: str | None
    entries: tuple[ForgetEntryView, ...]
    needs_confirmation: bool
    confirmation_token: str | None
    expires_at: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ForgetResult:
    action: str
    target: str
    entry_count: int
    status: str


async def propose_forget(
    *,
    owner_id: str,
    target: str,
    action: str,
    memory: MemoryForgetter,
) -> ForgetProposal:
    preview = await memory.forget_preview(
        owner_id=owner_id, target=target, action=action
    )
    return ForgetProposal(
        status=preview.status,
        target=preview.target,
        action=preview.action,
        entries=tuple(
            ForgetEntryView(
                entry_id=entry.drawer_id,
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
        action=outcome.action,
        target=outcome.target,
        entry_count=outcome.entry_count,
        status=outcome.status,
    )
