"""Signing every device out, and why that is safe to offer.

One action, and it is the one a person reaches for when a phone goes missing.
What makes it offerable rather than dangerous is the shape underneath: the
runtime stores the instant it revoked at and refuses tokens issued before it, so
every device reconnects with a fresh token on its own. Until that was true
(``eidolon_sdk@6c24516``) the same call locked an Owner's whole namespace out
permanently, which is not a button anybody should be given.

Two things this layer deliberately does not do.

**It does not confirm.** Whether to ask twice is a question about a screen, not
about a boundary, and a confirmation implemented here would be a dialog the wire
cannot render. The mobile client asks; this relays.

**It does not touch Controller access.** Runtime sessions are what devices use to
talk to a Companion; a Controller grant is what a phone uses to manage this Host.
Revoking the first leaves the second alone — and saying so is most of what the
copy above this has to get right, because "sign every device out" reads like it
might lock the person out of their own management app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import RuntimeSessionRevocation


@runtime_checkable
class RuntimeSessionRevoker(Protocol):
    """The one authority call this needs."""

    async def revoke_runtime_sessions(
        self, *, owner_id: str
    ) -> RuntimeSessionRevocation: ...


@dataclass(frozen=True, slots=True)
class RevokedRuntimeSessions:
    #: When it happened. Relayed rather than re-stamped here: the instant that
    #: matters is the one the runtime compares tokens against, and a second
    #: clock's opinion of "now" would be a different answer.
    revoked_at: str


async def revoke_runtime_sessions(
    *,
    owner_id: str,
    sessions: RuntimeSessionRevoker,
) -> RevokedRuntimeSessions:
    answer = await sessions.revoke_runtime_sessions(owner_id=owner_id)
    return RevokedRuntimeSessions(revoked_at=answer.revoked_at)
