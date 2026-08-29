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

from eidolon_admin_server.app.control_plane.contracts import (
    MemoryBrowse,
    MemoryEntries,
    MemoryExport,
    MemoryGraph,
    MemoryGraphEdge,
    MemoryGraphNode,
    MemoryMaterialization,
    MemoryStatus,
)


@runtime_checkable
class MemoryBrowser(Protocol):
    """The three authority reads these projections need."""

    async def browse(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryBrowse: ...

    async def status(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryStatus: ...

    async def entries(
        self,
        *,
        owner_id: str,
        since: str,
        limit: int | None = None,
        companion_id: str | None = None,
    ) -> MemoryEntries: ...

    async def graph(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryGraph: ...

    async def export(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryExport: ...


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
    memory_realm_id: str
    audience_scope: str
    materialization: MemoryMaterialization
    wings: tuple[MemoryWingView, ...]
    entry_count: int
    withheld_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class MemoryGraphView:
    nodes: tuple[MemoryGraphNode, ...]
    edges: tuple[MemoryGraphEdge, ...]
    truncated: bool


async def read_graph(
    *,
    owner_id: str,
    companion_id: str | None,
    memory: MemoryBrowser,
) -> MemoryGraphView:
    graph = await memory.graph(owner_id=owner_id, companion_id=companion_id)
    return MemoryGraphView(
        nodes=graph.nodes,
        edges=graph.edges,
        truncated=graph.truncated,
    )


async def read_library(
    *,
    owner_id: str,
    companion_id: str | None,
    memory: MemoryBrowser,
) -> MemoryLibrary:
    page = await memory.browse(owner_id=owner_id, companion_id=companion_id)
    return MemoryLibrary(
        memory_realm_id=page.memory_space_id,
        audience_scope=page.audience_scope,
        materialization=page.materialization,
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


@dataclass(frozen=True, slots=True)
class MemoryEntryView:
    entry_id: str
    recorded_at: str
    recorded_at_source: str
    wing_id: str
    room_id: str
    preview: str


@dataclass(frozen=True, slots=True)
class MemoryDay:
    """What was recorded in a window the caller named.

    The window itself is carried back. A client that asked for "since my last
    visit" and got a list with no ``since`` could not tell an empty day from an
    answer to a different question.
    """

    since: str
    entries: tuple[MemoryEntryView, ...]
    entry_count: int
    more_in_window: bool
    undated_count: int
    truncated: bool


async def read_day(
    *,
    owner_id: str,
    since: str,
    limit: int | None,
    companion_id: str | None,
    memory: MemoryBrowser,
) -> MemoryDay:
    """Recent entries, as the realm reports them.

    ``since`` is relayed rather than defaulted. A day depends on where the
    person is; a default here would be this layer answering for a timezone it
    does not know, and it would be wrong by up to a day without saying so.
    """

    page = await memory.entries(
        owner_id=owner_id, since=since, limit=limit, companion_id=companion_id
    )
    return MemoryDay(
        since=page.since,
        entries=tuple(
            MemoryEntryView(
                entry_id=entry.entry_id,
                recorded_at=entry.recorded_at,
                recorded_at_source=entry.recorded_at_source,
                wing_id=entry.wing_id,
                room_id=entry.room_id,
                preview=entry.preview,
            )
            for entry in page.entries
        ),
        entry_count=page.entry_count,
        more_in_window=page.more_in_window,
        undated_count=page.undated_count,
        truncated=page.truncated,
    )


@dataclass(frozen=True, slots=True)
class MemoryExportRecordView:
    entry_id: str
    recorded_at: str
    recorded_at_source: str
    wing_id: str
    room_id: str
    memory_type: str
    value: str


@dataclass(frozen=True, slots=True)
class MemoryCopy:
    """The whole visible memory, as a person would keep it.

    The one projection on this surface that must not shorten anything. Its two
    counts are what keep it honest: ``undated_count`` says why some of it carries
    no date, and ``truncated`` says the palace scan stopped before the end. A
    file that was silently part of a memory would be worse than one that says it
    is part.
    """

    taken_at: str
    records: tuple[MemoryExportRecordView, ...]
    record_count: int
    undated_count: int
    truncated: bool


async def read_copy(
    *,
    owner_id: str,
    companion_id: str | None,
    memory: MemoryBrowser,
) -> MemoryCopy:
    """A relay, like the other two, and for the same reason.

    Assembling the file here would assemble it out of whatever this process
    happened to ask for; the realm is the only thing that knows what a complete
    answer to "everything I can see" is.
    """

    page = await memory.export(owner_id=owner_id, companion_id=companion_id)
    return MemoryCopy(
        taken_at=page.taken_at,
        records=tuple(
            MemoryExportRecordView(
                entry_id=record.entry_id,
                recorded_at=record.recorded_at,
                recorded_at_source=record.recorded_at_source,
                wing_id=record.wing_id,
                room_id=record.room_id,
                memory_type=record.memory_type,
                value=record.value,
            )
            for record in page.records
        ),
        record_count=page.record_count,
        undated_count=page.undated_count,
        truncated=page.truncated,
    )
