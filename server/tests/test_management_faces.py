"""What an Eidolon looks like, over the management surface.

Three things worth holding, and none of them is "an image comes back":

- **ownership is proved before the authority is asked**, because the face routes
  are keyed on a Companion alone — the same shape as persona, and the reason
  this projection exists at all;
- **a photograph is not sent twice.** The hop that matters is the one to a phone
  over a house's wifi, and an ``If-None-Match`` that still matches costs no
  bytes;
- **what may be a face is the authority's to say.** Nothing here re-checks that
  it is a JPEG, or how large it may be, so there is one answer rather than two
  that drift.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionFace,
    CompanionIdentity,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.faces import (
    clear_face,
    read_face,
    read_face_state,
    set_face,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.management.router import ManagementBackendError

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_FACE = b"\xff\xd8a face\xff\xd9"
_DIGEST = hashlib.sha256(_FACE).hexdigest()


class _Companions:
    def __init__(self) -> None:
        self.proved: list[tuple[str, str]] = []

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        raise AssertionError("faces never list")

    async def get_owner_companion(self, owner_id, companion_id):
        self.proved.append((owner_id, companion_id))
        if companion_id != "companion-a":
            raise AuthorityFailure("data", "not_found", "companion not found", 404)
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id=owner_id,
            display_name="小忆",
            lifecycle_state="active",
            kind="conversational",
            revision=2,
        )


class _Faces:
    def __init__(self, face: bytes | None = _FACE) -> None:
        self.face = face
        self.calls: list[str] = []

    async def get_companion_face_state(self, companion_id: str) -> CompanionFace:
        self.calls.append("state")
        return self._state(companion_id)

    async def get_companion_face(self, companion_id: str) -> bytes | None:
        self.calls.append("bytes")
        return self.face

    async def set_companion_face(self, companion_id: str, face: bytes) -> CompanionFace:
        self.calls.append("set")
        self.face = face
        return self._state(companion_id)

    async def clear_companion_face(self, companion_id: str) -> CompanionFace:
        self.calls.append("clear")
        self.face = None
        return self._state(companion_id)

    def _state(self, companion_id: str) -> CompanionFace:
        if self.face is None:
            return CompanionFace(
                operation="companion.face", companion_id=companion_id, has_face=False
            )
        return CompanionFace(
            operation="companion.face",
            companion_id=companion_id,
            has_face=True,
            face_asset_id="face-1",
            sha256=hashlib.sha256(self.face).hexdigest(),
            size_bytes=len(self.face),
            updated_at="2026-08-25T09:00:00Z",
        )


async def test_a_face_is_only_read_for_a_companion_of_this_owner() -> None:
    """The authority's face routes name a Companion and not an Owner, so this
    is where "someone else's" becomes a 404 — before any bytes are fetched."""

    companions = _Companions()
    faces = _Faces()

    got = await read_face(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=companions,
        faces=faces,
    )
    assert got.content == _FACE
    assert got.sha256 == _DIGEST
    assert companions.proved == [("owner-1", "companion-a")]

    with pytest.raises(AuthorityFailure) as refused:
        await read_face(
            owner_id="owner-1",
            companion_id="someone-elses",
            companions=companions,
            faces=faces,
        )
    assert refused.value.status_code == 404
    assert faces.calls == ["bytes"], "nothing was fetched for a Companion not theirs"


async def test_the_face_a_caller_already_holds_is_not_sent_again() -> None:
    got = await read_face(
        owner_id="owner-1",
        companion_id="companion-a",
        known_sha256=_DIGEST,
        companions=_Companions(),
        faces=_Faces(),
    )

    assert got.unchanged is True
    assert got.content is None
    # Still says which one it is, so a caller that lost track of its own copy
    # learns what it is holding rather than having to ask again.
    assert got.sha256 == _DIGEST


async def test_an_eidolon_with_no_face_is_a_state_and_not_a_failure() -> None:
    got = await read_face(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=_Companions(),
        faces=_Faces(face=None),
    )

    assert got.content is None
    assert got.sha256 is None
    assert got.unchanged is False


async def test_giving_and_taking_away_a_face_answer_with_the_state() -> None:
    faces = _Faces(face=None)
    companions = _Companions()

    given = await set_face(
        owner_id="owner-1",
        companion_id="companion-a",
        face=_FACE,
        companions=companions,
        faces=faces,
    )
    assert given.has_face is True
    assert given.sha256 == _DIGEST

    taken = await clear_face(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=companions,
        faces=faces,
    )
    assert taken.has_face is False
    assert taken.sha256 is None

    state = await read_face_state(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=companions,
        faces=faces,
    )
    assert state.has_face is False


class _Backend:
    def __init__(self) -> None:
        self.asked: list[dict] = []
        self.face: bytes | None = _FACE

    async def companion_face_state(self, *, owner_id, companion_id) -> dict:
        return {
            "contract_version": "1",
            "operation": "companion.face",
            "companion_id": companion_id,
            "has_face": self.face is not None,
            "sha256": _DIGEST if self.face else None,
            "updated_at": None,
        }

    async def companion_face(self, *, owner_id, companion_id, known_etag):
        self.asked.append({"companion_id": companion_id, "known_etag": known_etag})
        if self.face is None:
            return 204, b"", None
        if known_etag == f'"sha256:{_DIGEST}"':
            return 304, b"", f'"sha256:{_DIGEST}"'
        return 200, self.face, f'"sha256:{_DIGEST}"'

    async def set_companion_face(self, *, owner_id, companion_id, face) -> dict:
        self.asked.append({"companion_id": companion_id, "bytes": len(face)})
        self.face = face
        return await self.companion_face_state(
            owner_id=owner_id, companion_id=companion_id
        )

    async def clear_companion_face(self, *, owner_id, companion_id) -> dict:
        self.face = None
        return await self.companion_face_state(
            owner_id=owner_id, companion_id=companion_id
        )

    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"this test never calls {name}")

        return unused


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


def _app(tmp_path: Path, backend):
    unused = _Unused()
    return create_app(
        LocalApiSettings(
            bootstrap=BootstrapSettings(
                mode=BootstrapMode.DEVELOPMENT,
                state_dir=tmp_path / "state",
                runtime_dir=tmp_path / "run",
                control_socket=tmp_path / "run/control.sock",
                ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
            )
        ),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=unused,  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
        management_backend=backend,
    )


def _stub_controller(monkeypatch, *, owner_id: str | None) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "reset_epoch": 0,
    }
    if owner_id is not None:
        principal["owner_id"] = owner_id

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)


async def _authenticate(client: httpx.AsyncClient) -> dict[str, str]:
    session = await client.post(
        "/api/local/v1/auth/sessions",
        json={
            "contract_version": "1",
            "purpose": "eidolon-controller-local-auth-v1",
            "controller_id": _CONTROLLER_ID,
            "challenge": _AUTH_CHALLENGE,
            "reset_epoch": 0,
            "signature": "abcdefgh",
        },
    )
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


async def test_the_face_travels_as_bytes_and_is_not_sent_twice(
    tmp_path, monkeypatch
) -> None:
    """Bytes stay bytes the whole way, and the second read costs none of them.

    Encoding a photograph into JSON at any hop would spend a megabyte to say
    what the bytes already say, and no layer between the authority and the phone
    has any use for what is inside it.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    path = "/api/management/v1/companions/companion-a/face"
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(path)
        headers = await _authenticate(client)
        first = await client.get(path, headers=headers)
        again = await client.get(
            path, headers={**headers, "If-None-Match": first.headers["ETag"]}
        )

    assert anonymous.status_code == 401
    assert first.status_code == 200
    assert first.content == _FACE
    assert first.headers["content-type"] == "image/jpeg"
    assert again.status_code == 304
    assert again.content == b""
    assert [call["known_etag"] for call in backend.asked] == [
        None,
        f'"sha256:{_DIGEST}"',
    ]


async def test_an_eidolon_without_a_face_answers_no_content(
    tmp_path, monkeypatch
) -> None:
    """204 rather than 404: the Eidolon is there, and it has no picture yet."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    backend.face = None
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(
            "/api/management/v1/companions/companion-a/face", headers=headers
        )

    assert answered.status_code == 204


async def test_giving_a_face_sends_the_photograph_as_the_body(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    backend.face = None
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    path = "/api/management/v1/companions/companion-a/face"
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        given = await client.put(path, content=_FACE, headers=headers)
        taken = await client.delete(path, headers=headers)

    assert given.status_code == 200
    assert given.json()["has_face"] is True
    assert backend.asked[0]["bytes"] == len(_FACE)
    assert taken.status_code == 200
    assert taken.json()["has_face"] is False


async def test_a_refused_face_is_relayed_with_the_hosts_status(
    tmp_path, monkeypatch
) -> None:
    """Whether these bytes are a face is the authority's answer, not this one's.

    A size or format rule restated here would be a second answer to one
    question, and the two would drift the first time either moved.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")

    class _Refusing(_Backend):
        async def set_companion_face(self, *, owner_id, companion_id, face) -> dict:
            raise ManagementBackendError("not a JPEG", status_code=415)

    transport = httpx.ASGITransport(app=_app(tmp_path, _Refusing()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        refused = await client.put(
            "/api/management/v1/companions/companion-a/face",
            content=b"not a photograph",
            headers=headers,
        )

    assert refused.status_code == 415
    assert refused.json()["detail"] == "not a JPEG"


async def test_someone_elses_eidolon_is_absent_on_every_face_verb(
    tmp_path, monkeypatch
) -> None:
    """Absent, not forbidden — on all four, or an id can be probed with whichever
    one was forgotten. The decision is made once, in the projection that proves
    ownership; this is the relay keeping it intact."""

    _stub_controller(monkeypatch, owner_id="owner-1")

    class _NotYours(_Backend):
        async def companion_face_state(self, *, owner_id, companion_id) -> dict:
            raise ManagementBackendError("companion not found", status_code=404)

        async def companion_face(self, *, owner_id, companion_id, known_etag):
            raise ManagementBackendError("companion not found", status_code=404)

        async def set_companion_face(self, *, owner_id, companion_id, face) -> dict:
            raise ManagementBackendError("companion not found", status_code=404)

        async def clear_companion_face(self, *, owner_id, companion_id) -> dict:
            raise ManagementBackendError("companion not found", status_code=404)

    transport = httpx.ASGITransport(app=_app(tmp_path, _NotYours()))
    path = "/api/management/v1/companions/someone-elses/face"
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answers = [
            (await client.get(path, headers=headers)).status_code,
            (await client.get(f"{path}-state", headers=headers)).status_code,
            (await client.put(path, content=_FACE, headers=headers)).status_code,
            (await client.delete(path, headers=headers)).status_code,
        ]

    assert answers == [404, 404, 404, 404]
