"""Naming: an Eidolon, and the person it belongs to.

Small writes, and the ways they go wrong are not small:

- a rename route keyed on a Companion alone, renaming someone else's;
- a name of spaces accepted, erasing the one they had;
- a client naming which Owner to rename;
- the projection trimming or "improving" a name — an Eidolon's name is the
  Owner's word for it, and this layer has no opinion about it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    OwnerIdentity,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.naming import rename_companion, rename_owner
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"


class _Companions:
    def __init__(self) -> None:
        self.proved: list[tuple[str, str]] = []

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        raise AssertionError("naming never lists")

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


class _Namer:
    def __init__(self) -> None:
        self.named: list[tuple[str, str]] = []

    async def rename_companion(self, companion_id, display_name):
        self.named.append((companion_id, display_name))
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id="owner-1",
            display_name=display_name,
            lifecycle_state="active",
            kind="conversational",
            revision=3,
        )

    async def rename_owner(self, owner_id, display_name):
        self.named.append((owner_id, display_name))
        return OwnerIdentity(
            operation="owner.identity",
            owner_id=owner_id,
            display_name=display_name,
            lifecycle_state="active",
            default_companion_id="companion-a",
            revision=8,
        )


async def test_ownership_is_proved_by_the_authority_before_anything_is_written() -> None:
    """The rename route is keyed on a Companion alone and says nothing about
    whose it is. Asking the owner-scoped route first is what turns "someone
    else's" into a 404 — proved there rather than compared here, so there is no
    second adjudicator to disagree with the first."""

    companions = _Companions()
    namer = _Namer()

    renamed = await rename_companion(
        owner_id="owner-1",
        companion_id="companion-a",
        display_name="阿力",
        companions=companions,
        namer=namer,
    )
    assert companions.proved == [("owner-1", "companion-a")]
    assert namer.named == [("companion-a", "阿力")]
    assert renamed.display_name == "阿力"
    # It moved, and the answer says so: a client holding the old one would fail
    # its next compare-and-set for a reason it could not see.
    assert renamed.revision == 3

    with pytest.raises(AuthorityFailure) as refused:
        await rename_companion(
            owner_id="owner-1",
            companion_id="someone-elses",
            display_name="阿力",
            companions=companions,
            namer=namer,
        )
    assert refused.value.status_code == 404
    assert namer.named == [("companion-a", "阿力")], "nothing was written"


async def test_an_owner_renames_only_themselves() -> None:
    """There is nothing to prove ownership against here, because the Owner *is*
    the scope: it arrives from the boundary that authenticated a Controller."""

    namer = _Namer()
    owner = await rename_owner(owner_id="owner-1", display_name="Manson", namer=namer)

    assert namer.named == [("owner-1", "Manson")]
    assert owner.display_name == "Manson"


class _Backend:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def rename_companion(self, *, owner_id, companion_id, display_name) -> dict:
        self.asked.append(
            {
                "owner_id": owner_id,
                "companion_id": companion_id,
                "display_name": display_name,
            }
        )
        return {
            "contract_version": "1",
            "operation": "companion.name",
            "companion_id": companion_id,
            "display_name": display_name,
            "revision": 3,
        }

    async def rename_owner(self, *, owner_id, display_name) -> dict:
        self.asked.append({"owner_id": owner_id, "display_name": display_name})
        return {
            "contract_version": "1",
            "operation": "owner.name",
            "owner_id": owner_id,
            "display_name": display_name,
            "revision": 8,
        }

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


async def test_a_name_of_spaces_is_refused_at_the_boundary_a_person_types_into(
    tmp_path, monkeypatch
) -> None:
    """It passes a length check and would erase the name they have.

    Refused here rather than two services away: the answer is the same either
    way, and this is the boundary the person is actually talking to.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        blank = await client.patch(
            "/api/management/v1/companions/companion-a",
            json={"display_name": "   "},
            headers=headers,
        )
        owner_blank = await client.patch(
            "/api/management/v1/owner",
            json={"display_name": ""},
            headers=headers,
        )

    assert blank.status_code == 422
    assert owner_blank.status_code == 422
    assert backend.asked == []


async def test_the_name_a_person_typed_arrives_as_they_typed_it(
    tmp_path, monkeypatch
) -> None:
    """Surrounding space is not a name; everything else is.

    No case folding, no length "tidying", no substituting an identifier for an
    unusual name. What an Eidolon is called is the Owner's word for it.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.patch(
            "/api/management/v1/companions/companion-a",
            json={"display_name": "  小忆 🌙  "},
            headers=headers,
        )

    assert answered.status_code == 200
    assert answered.json()["display_name"] == "小忆 🌙"
    assert answered.json()["revision"] == 3
    assert backend.asked == [
        {
            "owner_id": "owner-1",
            "companion_id": "companion-a",
            "display_name": "小忆 🌙",
        }
    ]


async def test_an_owner_cannot_be_named_by_a_caller(tmp_path, monkeypatch) -> None:
    """There is exactly one Owner a session can speak for, so there is no path
    parameter and no body field for one — and a request that sends one is
    refused rather than quietly ignored."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.patch(
            "/api/management/v1/owner", json={"display_name": "Manson"}
        )
        headers = await _authenticate(client)
        answered = await client.patch(
            "/api/management/v1/owner",
            json={"display_name": "Manson"},
            headers=headers,
        )
        named_someone = await client.patch(
            "/api/management/v1/owner",
            json={"display_name": "Manson", "owner_id": "owner-2"},
            headers=headers,
        )

    assert anonymous.status_code == 401
    assert answered.status_code == 200
    assert answered.json() == {
        "contract_version": "1",
        "owner_id": "owner-1",
        "display_name": "Manson",
        "revision": 8,
    }
    assert named_someone.status_code == 422
    assert [call["owner_id"] for call in backend.asked] == ["owner-1"]


async def test_the_published_contract_declares_no_owner_parameter(tmp_path) -> None:
    """Ignoring a field is behaviour; declaring nothing is the contract — and the
    document is what two generated clients are built from."""

    created = _app(tmp_path, _Backend())
    paths = created.openapi()["paths"]
    for path in ("/api/management/v1/owner", "/api/management/v1/companions/{companion_id}"):
        for parameter in paths[path]["patch"].get("parameters", []):
            assert parameter["in"] != "query", (path, parameter)
            assert parameter["name"] != "owner_id", (path, parameter)
