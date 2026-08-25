"""The one read two management clients make before anything else.

``/context`` answers "who is this, what may they do here, and what are the
limits" in a single call, so neither client has to assemble a dozen requests and
guess whether a feature exists. Three rules shape it:

**Owner scope is not an input.** The Owner comes from the authenticated
Controller at the Local API boundary and is passed down; a client cannot name a
different one, and this layer never chooses one.

**Capabilities are discovery, not permission.** ``true`` means the Host can do
this at all — the code exists and its authority answers. Whether *this*
Controller may do it is a separate question, answered per action. A client that
treats a capability as an entitlement will get a 403 and should.

**A capability is false until its whole slice is closed.** Not "the route
exists", not "it mostly works" — false until authority, application, public
route, generated client and tests are all in place. False is what keeps a button
from appearing before the thing behind it works, and appearing-then-failing is
worse than absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import OwnerIdentity


@runtime_checkable
class OwnerReader(Protocol):
    """The Owner facts this read needs, and nothing else."""

    async def get_owner(self, owner_id: str) -> OwnerIdentity: ...


@dataclass(frozen=True, slots=True)
class ManagementContext:
    """What a client learns before it draws anything.

    ``default_companion_id`` is carried verbatim from the Owner aggregate.
    ``None`` is a real answer — an Owner whose only Companion is a guard, or
    whose default was archived — and no layer above may resolve it by picking a
    Companion.
    """

    owner_id: str
    owner_display_name: str
    owner_revision: int
    default_companion_id: str | None
    capabilities: dict[str, bool]
    #: Why each false capability is false, for the ones where there is a reason
    #: worth acting on. Present only for names in ``capabilities`` that are
    #: false; a false name absent from here has no further explanation.
    #:
    #: The distinction is the whole point. "This Host's software cannot do this
    #: yet" and "this Host was never given the key" look identical in a boolean
    #: and lead to completely different places — one is a release nobody has cut,
    #: the other is a command somebody can run tonight. On this product the Host
    #: owner and the phone owner are usually one person, so telling them apart is
    #: not diagnostics: it is the difference between waiting and fixing.
    unavailable: dict[str, str]
    limits: dict[str, int | None]


#: Everything the plan's v1 surface will eventually offer, declared here so a
#: client discovers the shape of the API rather than the shape of today's build.
#: A name missing from this map is a name the client has never heard of; a name
#: present and false is a thing this Host cannot do yet. The difference matters:
#: the first is a version skew, the second is a feature gate.
_CAPABILITIES: tuple[str, ...] = (
    "companion.read",
    "companion.create",
    "companion.rename",
    #: What the Owner is called. Its own name rather than part of
    #: ``controller.manage``: renaming yourself is not administering a Host.
    "owner.rename",
    "companion.set_default",
    #: What an Eidolon looks like: reading the face, giving it one, taking it
    #: away. Its own name because it is the one part of a Companion a person
    #: changes by choosing a photograph rather than by typing.
    "companion.face",
    "companion.archive",
    "companion.restore",
    "persona.read",
    "persona.govern",
    "memory.read",
    "memory.govern",
    "memory.export",
    "device.read",
    "device.manage",
    #: The Host's own record of what it did to this Owner's things. Its own name
    #: rather than part of ``conversation.read``: one is what an Eidolon said,
    #: the other is what was done to it.
    "activity.read",
    "conversation.read",
    "task.read",
    "task.manage",
    "host.read",
    "host.operate",
    "controller.manage",
    #: Ending every runtime session an Owner has. Its own name rather than part
    #: of ``controller.manage``: that one is about which phones may *manage* this
    #: Host, and this is about which sessions may *talk* to a Companion. A person
    #: who signs their devices out must not lose their management app with them.
    "session.revoke",
)

#: Closed slices. Everything else in ``_CAPABILITIES`` is false, and stays false
#: until its authority, application, route, client and tests are all in place.
#:
#: ``companion.read`` is the first one closed: Data answers the roster, the
#: application reads it, both boundaries expose it, both clients are generated
#: from the same document, and there are tests on each side. This frozenset is
#: the only place a capability turns on — a route existing is not enough, which
#: is what keeps a button from appearing before the thing behind it works.
_ENABLED: frozenset[str] = frozenset(
    {
        "companion.read",
        "companion.create",
        "companion.set_default",
    #: What an Eidolon looks like: reading the face, giving it one, taking it
    #: away. Its own name because it is the one part of a Companion a person
    #: changes by choosing a photograph rather than by typing.
    "companion.face",
        "memory.read",
        "memory.govern",
        # Persona history and going back to a chapter: Data answers, the
        # projection drops proposals, both boundaries expose it, both clients are
        # generated, and Mobile has the screen.
        "persona.read",
        "persona.govern",
        # The copy a person keeps, as distinct from the Host backup.
        "memory.export",
        # Putting one away and bringing it back. True only since a Companion
        # that is not active stopped being handed runtime snapshots
        # (eidolon_data@67b5cf6): before that, archiving changed a row and a
        # device that already knew the id kept talking to it, which is a button
        # that says it did something it did not do.
        "companion.archive",
        "companion.restore",
        # Naming: both of them, because the same slice closes both and a Host
        # that could name one but not the other would be an odd thing to
        # explain.
        "companion.rename",
        "owner.rename",
        # The face, over the same surface as everything else about a Companion.
        "companion.face",
        # Which phones may manage this Host. Answered by the Host's own trust
        # root rather than by an authority, which is why it is the one part of
        # this surface the LAN process serves directly.
        "controller.manage",
        # The machine itself: how it is doing, and its services. Beside
        # ``controller.manage`` and for the same reason — facts about this Host
        # rather than an authority's data.
        "host.read",
        "host.operate",
        # The governance facts Data writes in the same transaction as each
        # change. Nothing had ever read them.
        "activity.read",
        # The Agent answers these and owns the task state machine; this side
        # relays, including its refusals.
        "conversation.read",
        "task.read",
        "task.manage",
        # Signing every device out. Only safe to advertise since owner
        # revocation became a watermark rather than a permanent lockout
        # (eidolon_sdk@6c24516) — before that this flag would have been a button
        # that bricked every device an Owner has.
        "session.revoke",
    }
)


#: Which authority each closed slice actually needs to be reachable at all.
#:
#: The docstring at the top of this file has always said a capability means the
#: code exists *and its authority answers*. Only the first half was ever
#: checked, and a Host that had never been given the memory credential answered
#: ``memory.read: true`` — so the phone drew 记忆库, 今天记下的, 导出记忆 and
#: 让它忘掉, and every one of them failed the moment it was touched. That is the
#: exact failure the "false until the whole slice is closed" rule was written to
#: prevent; it was just enforced against the code and not against the Host.
#:
#: This is the half a single process can answer for free. Whether a credential
#: is *configured* is a local fact — no probe, no latency, no flapping — and it
#: is the half that was missing. Whether the authority is *up right now* stays a
#: per-action answer, because a capability that flickers with a restart would
#: make buttons appear and disappear under someone's thumb.
#:
#: A name absent from this map needs no authority: it is answered by Data (which
#: every Host has, or it has no Owner to ask) or by the Host's own trust root.
_CAPABILITY_AUTHORITIES: dict[str, frozenset[str]] = {
    "memory.read": frozenset({"memory"}),
    "memory.govern": frozenset({"memory"}),
    "memory.export": frozenset({"memory"}),
    "conversation.read": frozenset({"agent"}),
    "task.read": frozenset({"agent"}),
    "task.manage": frozenset({"agent"}),
    "session.revoke": frozenset({"agent"}),
}


#: Why a capability is being withheld. Two values, because there are two
#: reasons, and a client that could not tell them apart would have to show the
#: same shrug for both.
WITHHELD_NOT_BUILT = "not_built"
WITHHELD_HOST_NOT_CONFIGURED = "host_not_configured"

WITHHELD_REASONS: tuple[str, ...] = (
    WITHHELD_NOT_BUILT,
    WITHHELD_HOST_NOT_CONFIGURED,
)


@runtime_checkable
class AuthorityCredentials(Protocol):
    """Which authorities this Host holds a credential for.

    A port rather than a settings import, so this projection stays a pure
    function of what it is handed and a test can state a Host that holds one
    credential and not another — which is the state a real Host was in.
    """

    def configured_authorities(self) -> frozenset[str]: ...


async def read_context(
    *,
    owner_id: str,
    owners: OwnerReader,
    credentials: AuthorityCredentials,
) -> ManagementContext:
    held = credentials.configured_authorities()
    owner = await owners.get_owner(owner_id)
    unavailable: dict[str, str] = {}
    for name in _CAPABILITIES:
        if name not in _ENABLED:
            unavailable[name] = WITHHELD_NOT_BUILT
        elif not _CAPABILITY_AUTHORITIES.get(name, frozenset()) <= held:
            # Named as configuration rather than as a missing feature: the code
            # is here and closed, and what is absent is a key on this machine.
            unavailable[name] = WITHHELD_HOST_NOT_CONFIGURED
    return ManagementContext(
        owner_id=owner.owner_id,
        owner_display_name=owner.display_name,
        owner_revision=owner.revision,
        default_companion_id=owner.default_companion_id,
        capabilities={name: name not in unavailable for name in _CAPABILITIES},
        unavailable=unavailable,
        # Null rather than a number the client could hard-code. A limit nobody
        # has measured is not a limit; ``max_active_companions`` waits on a
        # capacity result, and until then the client must not invent one.
        limits={"max_active_companions": None},
    )
