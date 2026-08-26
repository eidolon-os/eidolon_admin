"""My devices, as a person reads them.

The composition is the interesting part: two authorities keep half the answer
each — Hub's Claim says whether a device is still mine, Kernel's mount says
which Eidolon answers through it — and the admission authority authorises by
*actor*, so this is the one management read that needs the authenticated
Controller and not only the Owner.

What the tests hold:

- **a name is never invented.** Nobody has named devices yet, so a row shows
  what the Manifest calls this kind of thing, and failing that the tail of the
  identifier — something a person can read out when asking for help;
- **online is never inferred.** An active Claim and a live mount both say the
  device is *known*; neither says it is switched on;
- **the names of Eidolons are a nicety, the devices are not.** A roster this
  read cannot get costs the names and nothing else;
- **a device this Owner does not hold is absent**, and checked before any
  mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.management.router import (
    ManagementBackendError,
    refusal_for_status,
)

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_DEVICES = "/api/management/v1/devices"
_KNOWN = "10:51:db:7e:24:44:aa:bb:cc:dd:ee:ff:00:11"
_OTHER = "10:51:db:7e:24:44:aa:bb:cc:dd:ee:ff:00:22"


def _device(
    *,
    device_id: str = _KNOWN,
    companion_id: str | None = "companion-a",
    kind: str = "atk-dnesp32s3",
    state: str = "active",
):
    return SimpleNamespace(
        device_id=device_id,
        claim=SimpleNamespace(
            device_ref=SimpleNamespace(
                device_instance_id=device_id,
                claim_generation=3,
                trust_epoch=2,
                owner_domain_generation=1,
            ),
            manifest_ref=SimpleNamespace(manifest_id=kind, revision=7),
            state=SimpleNamespace(value=state),
            updated_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        body=SimpleNamespace(
            body_endpoint_id=f"{device_id}:body",
            mount_revision=4,
            assignment_revision=0 if companion_id is None else 4,
            answering_companion_id=companion_id,
            selection_provenance=None if companion_id is None else "user_selected",
        ),
    )


class _Devices:
    def __init__(self, *devices, refuse: ManagementBackendError | None = None) -> None:
        self.devices = list(devices)
        self.refuse = refuse
        self.asked: list[dict] = []
        self.changed: list[dict] = []

    async def list_devices(self, *, session):
        self.asked.append(
            {"owner_id": session.owner_id, "controller_id": session.controller_id}
        )
        if self.refuse is not None:
            raise self.refuse
        return SimpleNamespace(devices=tuple(self.devices))

    async def set_device_companion(
        self,
        *,
        session,
        device_id: str,
        companion_id: str | None,
        expected_assignment_revision: int,
        request_id: str,
    ):
        self.changed.append(
            {
                "device_id": device_id,
                "companion_id": companion_id,
                "expected_revision": expected_assignment_revision,
                "request_id": request_id,
            }
        )
        if device_id != _KNOWN:
            raise ManagementBackendError(
            "Device is not mounted",
            status_code=404,
            refusal=refusal_for_status(404, "Device is not mounted"),
        )
        return _device(companion_id=companion_id)


class _Backend:
    def __init__(self, *, roster_fails: bool = False) -> None:
        self.roster_fails = roster_fails

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict:
        if self.roster_fails:
            raise ManagementBackendError(
            "data is away",
            status_code=503,
            refusal=refusal_for_status(503, "data is away"),
        )
        return {
            "companions": [
                {"companion_id": "companion-a", "display_name": "小忆"},
                {"companion_id": "companion-b", "display_name": "阿力"},
            ]
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


def _app(tmp_path: Path, devices, backend=None):
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
        management_backend=backend or _Backend(),
        owner_device_port=devices,
    )


def _stub_controller(monkeypatch) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "owner_id": "owner-1",
        "reset_epoch": 0,
    }

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


async def test_a_device_reads_as_what_it_is_and_who_answers_through_it(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    devices = _Devices(_device())
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_DEVICES)
        headers = await _authenticate(client)
        answered = await client.get(_DEVICES, headers=headers)

    assert anonymous.status_code == 401
    row = answered.json()["devices"][0]
    assert row["label"] == "atk-dnesp32s3"
    assert row["state"] == "ready"
    assert row["answers_as_companion_name"] == "小忆"
    # The composition needs the Controller, not only the Owner: the admission
    # authority authorises by actor.
    assert devices.asked == [{"owner_id": "owner-1", "controller_id": _CONTROLLER_ID}]
    # Kept underneath, because a person asking for help will be asked for them.
    assert row["claim_generation"] == 3
    assert row["trust_epoch"] == 2
    assert row["manifest_revision"] == 7
    assert row["revision"] == 4
    # And what this list does not know is said, not implied.
    assert "还在等你确认" in answered.json()["coverage"]


async def test_online_is_never_inferred_from_a_claim_or_a_mount(
    tmp_path, monkeypatch
) -> None:
    """Both say the device is *known*. Neither says it is switched on, and a
    screen that read one as the other would tell someone their unplugged
    speaker is fine."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, _Devices(_device())))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        row = (await client.get(_DEVICES, headers=headers)).json()["devices"][0]

    assert row["online"] == "unknown"
    assert row["online_reason"]


async def test_a_device_nobody_named_falls_back_to_its_identifier(
    tmp_path, monkeypatch
) -> None:
    """Never invented into a name, and never left blank: the tail is something
    a person can read out loud."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(
        app=_app(tmp_path, _Devices(_device(kind="", companion_id=None)))
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        row = (await client.get(_DEVICES, headers=headers)).json()["devices"][0]

    assert row["label"] == f"…{_KNOWN[-12:]}"
    assert row["kind"] == ""
    assert row["state"] == "awaiting_companion"
    assert row["answers_as_companion_id"] is None
    assert row["answers_as_companion_name"] == ""


async def test_a_roster_that_cannot_be_read_costs_the_names_and_nothing_else(
    tmp_path, monkeypatch
) -> None:
    """Which devices are mine does not depend on what my Eidolons are called."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(
        app=_app(tmp_path, _Devices(_device()), _Backend(roster_fails=True))
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_DEVICES, headers=headers)

    assert answered.status_code == 200
    row = answered.json()["devices"][0]
    assert row["answers_as_companion_id"] == "companion-a"
    assert row["answers_as_companion_name"] == ""


async def test_pointing_a_device_at_an_eidolon_carries_the_revision_and_the_id(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    devices = _Devices(_device())
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        pointed = await client.put(
            f"{_DEVICES}/{_KNOWN}/companion",
            json={
                "companion_id": "companion-b",
                "expected_revision": 4,
                "request_id": "attach-1",
            },
            headers=headers,
        )
        released = await client.put(
            f"{_DEVICES}/{_KNOWN}/companion",
            json={"companion_id": None, "expected_revision": 4, "request_id": "let-go-1"},
            headers=headers,
        )
        elsewhere = await client.put(
            f"{_DEVICES}/not-mine/companion",
            json={"expected_revision": 4, "request_id": "attach-2"},
            headers=headers,
        )

    assert pointed.status_code == 200
    assert pointed.json()["answers_as_companion_name"] == "阿力"
    assert released.status_code == 200
    assert released.json()["state"] == "awaiting_companion"
    # Absent rather than forbidden, so an identifier cannot be probed.
    assert elsewhere.status_code == 404
    assert [call["request_id"] for call in devices.changed] == [
        "attach-1",
        "let-go-1",
        "attach-2",
    ]
    assert all(call["expected_revision"] == 4 for call in devices.changed)


async def test_taking_a_device_off_says_which_facts_are_settled(
    tmp_path, monkeypatch
) -> None:
    """Not a percentage: which specific things are true yet.

    Removal converges across three authorities on their own schedule, and this
    Host does not stand in the middle pretending it is one step. ``unfinished``
    is the normal case and must not read as either done or refused.
    """

    _stub_controller(monkeypatch)

    class _Removing(_Devices):
        async def remove_device(self, *, session, device_id: str, request_id: str):
            self.changed.append({"device_id": device_id, "request_id": request_id})
            return SimpleNamespace(
                device_id=device_id,
                request_id=request_id,
                outcome="unfinished",
                conditions=(
                    SimpleNamespace(
                        name="hub_claim_revoked",
                        state=SimpleNamespace(value="met"),
                        authority="hub",
                        observed_at=datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
                    ),
                    SimpleNamespace(
                        name="device_erase_acknowledged",
                        state=SimpleNamespace(value="pending"),
                        authority="device-control",
                        observed_at=None,
                    ),
                ),
            )

    devices = _Removing(_device())
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        removed = await client.post(
            f"{_DEVICES}/{_KNOWN}/removal",
            json={"request_id": "removal-1"},
            headers=headers,
        )

    body = removed.json()
    assert removed.status_code == 200
    assert body["outcome"] == "unfinished"
    assert [condition["state"] for condition in body["conditions"]] == ["met", "pending"]
    # A condition nobody has observed yet says so with an empty time rather than
    # a made-up one.
    assert body["conditions"][1]["observed_at"] == ""
    assert devices.changed == [{"device_id": _KNOWN, "request_id": "removal-1"}]


async def test_a_body_nobody_decided_about_carries_zero_and_can_still_be_pointed(
    tmp_path, monkeypatch
) -> None:
    """Zero is a value the phone sends, not a value it is missing.

    A device that has just been claimed has a Body and no assignment. The first
    change to it compares against zero, and refusing to accept zero would make
    the only device state a person ever starts from the one state they could not
    act on.
    """

    _stub_controller(monkeypatch)
    devices = _Devices(_device(companion_id=None))
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        listed = await client.get(_DEVICES, headers=headers)
        pointed = await client.put(
            f"{_DEVICES}/{_KNOWN}/companion",
            json={
                "companion_id": "companion-b",
                "expected_revision": 0,
                "request_id": "first-choice",
            },
            headers=headers,
        )

    assert listed.json()["devices"][0]["revision"] == 0
    assert listed.json()["devices"][0]["quiet_because"] == ""
    assert pointed.status_code == 200
    assert devices.changed[0]["expected_revision"] == 0


async def test_a_speaker_that_went_quiet_says_which_way_it_went_quiet(
    tmp_path, monkeypatch
) -> None:
    """The three ways of answering as nobody are not one state.

    They leave the same empty assignment behind. A person who cleared a speaker
    knows why it is silent; a person whose Eidolon was put away is owed the
    sentence; and a speaker nobody ever pointed anywhere was never quiet at all.
    A screen with only "nobody answers" would have to say the same thing to all
    three, and one of them would read as a fault.
    """

    _stub_controller(monkeypatch)
    cleared = _device(companion_id=None)
    cleared.body.selection_provenance = "user_cleared"
    put_away = _device(device_id=_OTHER, companion_id=None)
    put_away.body.selection_provenance = "companion_deleted"
    devices = _Devices(cleared, put_away)
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        listed = await client.get(_DEVICES, headers=headers)

    rows = {row["device_id"]: row for row in listed.json()["devices"]}
    assert rows[_KNOWN]["quiet_because"] == "you_cleared_it"
    assert rows[_OTHER]["quiet_because"] == "companion_put_away"
    # And a Body that is answering says nothing about being quiet.
    assert all(row["state"] == "awaiting_companion" for row in rows.values())
