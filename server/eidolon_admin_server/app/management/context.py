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


async def read_context(*, owner_id: str, owners: OwnerReader) -> ManagementContext:
    owner = await owners.get_owner(owner_id)
    return ManagementContext(
        owner_id=owner.owner_id,
        owner_display_name=owner.display_name,
        owner_revision=owner.revision,
        default_companion_id=owner.default_companion_id,
        capabilities={name: name in _ENABLED for name in _CAPABILITIES},
        # Null rather than a number the client could hard-code. A limit nobody
        # has measured is not a limit; ``max_active_companions`` waits on a
        # capacity result, and until then the client must not invent one.
        limits={"max_active_companions": None},
    )
