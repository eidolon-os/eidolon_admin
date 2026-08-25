"""What an Eidolon looks like: reading its face, giving it one, taking it away.

The face is not part of a Companion's identity read. It is bytes — a photograph
that a screen fetches once and keeps — and mixing it into a JSON read would put
a megabyte behind a question about a name.

**Ownership is proved here, and it has to be.** The authority's face routes are
keyed on a Companion alone, exactly like the persona ones, so asking the
owner-scoped Companion route first is what turns "someone else's Eidolon" into a
404. That is the difference between this module and the lifecycle one, whose
authority route takes the Owner itself and needs no help.

**Nothing here decides what a face may be.** JPEG-or-not, how large, whether the
bytes are what they claim: all of that is the authority's, and restating it here
would be two answers to "is this a face" that can drift apart.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import CompanionFace
from eidolon_admin_server.app.management.roster import RosterReader


@runtime_checkable
class CompanionFaceKeeper(Protocol):
    """The four authority calls this needs."""

    async def get_companion_face_state(self, companion_id: str) -> CompanionFace: ...

    async def get_companion_face(self, companion_id: str) -> bytes | None: ...

    async def set_companion_face(
        self, companion_id: str, face: bytes
    ) -> CompanionFace: ...

    async def clear_companion_face(self, companion_id: str) -> CompanionFace: ...


@dataclass(frozen=True, slots=True)
class FaceView:
    """Whether there is a face, and which one — never the face itself."""

    companion_id: str
    has_face: bool
    #: Changes whenever the face does, which is how a client knows its copy is
    #: stale without comparing photographs.
    sha256: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class FaceBytes:
    """A face, or the absence of one, or "the one you already have".

    Three states rather than two, because a phone that keeps a face should not
    be sent it again on every screen open. ``unchanged`` is what an
    ``If-None-Match`` that still matches produces, and it costs no bytes.
    """

    content: bytes | None
    sha256: str | None
    unchanged: bool = False


async def read_face(
    *,
    owner_id: str,
    companion_id: str,
    known_sha256: str | None = None,
    companions: RosterReader,
    faces: CompanionFaceKeeper,
) -> FaceBytes:
    """The face of one of this Owner's Eidolons.

    ``known_sha256`` is what the caller already holds. The comparison happens
    after the bytes are fetched from the authority rather than before, because
    the hop that matters is the one to the phone: a loopback read costs almost
    nothing and asking twice — once for a hash, once for the bytes — would make
    the common case slower to make the rare case cheaper.
    """

    await companions.get_owner_companion(owner_id, companion_id)
    face = await faces.get_companion_face(companion_id)
    if face is None:
        return FaceBytes(content=None, sha256=None)
    digest = hashlib.sha256(face).hexdigest()
    if known_sha256 and known_sha256 == digest:
        return FaceBytes(content=None, sha256=digest, unchanged=True)
    return FaceBytes(content=face, sha256=digest)


async def read_face_state(
    *,
    owner_id: str,
    companion_id: str,
    companions: RosterReader,
    faces: CompanionFaceKeeper,
) -> FaceView:
    """Whether it is worth fetching a photograph, without fetching one."""

    await companions.get_owner_companion(owner_id, companion_id)
    return _view(await faces.get_companion_face_state(companion_id))


async def set_face(
    *,
    owner_id: str,
    companion_id: str,
    face: bytes,
    companions: RosterReader,
    faces: CompanionFaceKeeper,
) -> FaceView:
    await companions.get_owner_companion(owner_id, companion_id)
    return _view(await faces.set_companion_face(companion_id, face))


async def clear_face(
    *,
    owner_id: str,
    companion_id: str,
    companions: RosterReader,
    faces: CompanionFaceKeeper,
) -> FaceView:
    """Take the face away, and leave the Eidolon.

    Deliberately its own action rather than "set an empty face": an Eidolon with
    no picture is a state someone chooses, and a screen has to be able to say
    which one it is looking at.
    """

    await companions.get_owner_companion(owner_id, companion_id)
    return _view(await faces.clear_companion_face(companion_id))


def _view(face: CompanionFace) -> FaceView:
    return FaceView(
        companion_id=face.companion_id,
        has_face=face.has_face,
        sha256=face.sha256,
        updated_at=face.updated_at,
    )
