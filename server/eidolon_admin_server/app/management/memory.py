"""What a person's memory holds, projected for a management client.

Thin, and the thinness is the design. The realm decides what may be seen — the
same policy its Eidolon's recall uses — so this layer filters nothing: a second
filter would be a second answer to "what may this person see", and the two would
drift until what someone sees depended on which screen they opened.

What it does do is *stop carrying* what a person has no use for. The realm
answers with a memory space id, which is an identifier for a thing nobody can
open, act on, or name. It is dropped here rather than at the client, because the
client should not have to know that a field it can see is one it must not show.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import MemoryBrowse


@runtime_checkable
class MemoryBrowser(Protocol):
    """The one authority call this read needs."""

    async def browse(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryBrowse: ...


@dataclass(frozen=True, slots=True)
class MemoryRoomView:
    room_id: str
    entry_count: int
    titles: tuple[str, ...]
    more: bool


@dataclass(frozen=True, slots=True)
class MemoryWingView:
    wing_id: str
    display_name: str
    description: str
    entry_count: int
    rooms: tuple[MemoryRoomView, ...]


@dataclass(frozen=True, slots=True)
class MemoryLibrary:
    wings: tuple[MemoryWingView, ...]
    entry_count: int
    withheld_count: int
    truncated: bool


async def read_library(
    *,
    owner_id: str,
    companion_id: str | None,
    memory: MemoryBrowser,
) -> MemoryLibrary:
    page = await memory.browse(owner_id=owner_id, companion_id=companion_id)
    return MemoryLibrary(
        wings=tuple(
            MemoryWingView(
                wing_id=wing.wing_id,
                # Empty when this Host has never heard of the wing. Left empty
                # rather than filled with the identifier: a client showing
                # "Wing_FromALaterRelease" to a person is worse than a client
                # deciding what to call an unknown category.
                display_name=wing.display_name,
                description=wing.description,
                entry_count=wing.drawer_count,
                rooms=tuple(
                    MemoryRoomView(
                        room_id=room.room_id,
                        entry_count=room.drawer_count,
                        titles=tuple(
                            str(drawer.get("preview") or drawer.get("key") or "")
                            for drawer in room.drawers_preview
                        ),
                        more=room.preview_truncated,
                    )
                    for room in wing.rooms
                ),
            )
            for wing in page.wings
        ),
        entry_count=page.entry_count,
        withheld_count=page.withheld_count,
        truncated=page.truncated,
    )
