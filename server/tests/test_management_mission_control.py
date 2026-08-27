"""The Owner's runtime map, on the Owner's plane.

Mission Control has existed on this Host for a while — as the operator console's
reading, at ``/api/mission-control/*``, scoped by an ``owner_id`` query
parameter. The Owner's phone could not use it: that surface carries the runtime
blackboard, trace spans, evidence chains and a permission ledger, and its caller
names whichever Owner it likes.

So what these tests hold is the boundary, not the payload — the payload's shape
is the SDK contract's and ``test_owner_runtime_projection.py`` validates it:

* **the Owner comes from the session and from nowhere else.** No query
  parameter, no header, nothing a caller can set;
* **a Host that cannot compose the reading refuses in the shape every other
  management route refuses in**, so a client has one thing to understand;
* **and the reading is one round trip.** A phone assembling this from six
  endpoints would be six round trips, no single instant the map was true at, and
  every client re-implementing the same join.
"""

from __future__ import annotations

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
_SNAPSHOT = "/api/management/v1/mission-control/snapshot"
_ACTIVITIES = "/api/management/v1/mission-control/activities"


def _refused(message: str, status: int = 503) -> ManagementBackendError:
    return ManagementBackendError(
        message, status_code=status, refusal=refusal_for_status(status, message)
    )


def _payload(owner_id: str) -> dict:
    """A minimal reading. Shape is asserted where the projection is."""

    lane = {
        "state": "ok",
        "detail": "",
        "observed_at": "2026-08-26T12:30:00Z",
        "latency_ms": 9,
        "truncated": False,
        "items": [],
    }
    return {
        "contract_version": "1",
        "coverage": "owner-runtime",
        "generated_at": "2026-08-26T12:30:00Z",
        "asked_for": owner_id,
        "devices": lane,
        "activities": lane,
        "turns": lane,
        "jobs": lane,
        "memory": {**{k: lane[k] for k in ("state", "detail", "observed_at", "latency_ms")}, "value": None},
        "services": lane,
        "events": lane,
    }


class _Backend:
    """Records who was asked about, which is the whole point of these tests."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.asked: list[str] = []
        self.paged: list[tuple[str, str | None]] = []

    async def mission_control_snapshot(self, *, owner_id: str) -> dict:
        if self.fails:
            raise _refused("Admin control plane is away")
        self.asked.append(owner_id)
        return _payload(owner_id)

    async def mission_control_activities(
        self, *, owner_id: str, before: str | None
    ) -> dict:
        if self.fails:
            raise _refused("Admin control plane is away")
        self.paged.append((owner_id, before))
        return {
            "contract_version": "1",
            "coverage": "owner-interactions",
            "state": "ok",
            "detail": "",
            "items": [],
            "next_cursor": "opaque-2",
        }


class _Devices:
    """The Owner's device inventory, as this plane reads it from the session.

    Present here because the route joins existence with presence — the two facts
    have different authorities and only one of them is reachable from the process
    that composes Mission Control. What that join means is held in
    ``test_owner_devices_join.py``; this file only holds the boundary.
    """

    def __init__(self) -> None:
        self.sessions: list[str] = []

    async def list_devices(self, *, session):
        self.sessions.append(session.owner_id)
        return SimpleNamespace(devices=[])


class _Unused:
    def __getattr__(self, name: str):  # pragma: no cover - must never be reached
        raise AssertionError(f"this test should not need {name}")


def _app(tmp_path: Path, backend: _Backend, devices: _Devices | None = None):
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
        owner_device_port=devices or _Devices(),
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


async def test_the_owner_comes_from_the_session(tmp_path: Path, monkeypatch) -> None:
    _stub_controller(monkeypatch)
    backend = _Backend()
    devices = _Devices()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend, devices))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        headers = await _authenticate(client)
        answer = await client.get(_SNAPSHOT, headers=headers)

    assert answer.status_code == 200
    body = answer.json()
    assert body["coverage"] == "owner-runtime"
    # The session's Owner, not one the caller chose.
    assert backend.asked == ["owner-1"]
    assert body["asked_for"] == "owner-1"
    # Both halves of the devices lane are asked about the same Owner, and that
    # Owner is the session's.
    assert devices.sessions == ["owner-1"]


async def test_a_caller_cannot_ask_about_another_owner(tmp_path: Path, monkeypatch) -> None:
    _stub_controller(monkeypatch)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        headers = await _authenticate(client)
        answer = await client.get(
            _SNAPSHOT,
            params={"owner_id": "owner-somebody-else"},
            headers=headers,
        )

    assert answer.status_code == 200
    # The parameter the operator surface accepts is not read here. It is not
    # rejected either — there is nothing to reject, because this route has no
    # such input: the Owner is the session's.
    assert backend.asked == ["owner-1"]


async def test_without_a_session_there_is_no_reading(tmp_path: Path, monkeypatch) -> None:
    _stub_controller(monkeypatch)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        answer = await client.get(_SNAPSHOT)

    assert answer.status_code == 401
    assert backend.asked == []


async def test_a_host_that_cannot_compose_refuses_in_the_usual_shape(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend(fails=True)))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        headers = await _authenticate(client)
        answer = await client.get(_SNAPSHOT, headers=headers)

    assert answer.status_code == 503
    body = answer.json()
    # Same refusal envelope as every other management route: a client that can
    # read one refusal can read them all.
    assert "detail" in body or "refusal" in body


async def test_the_history_is_scoped_by_the_session_and_pages_opaquely(
    tmp_path: Path, monkeypatch
) -> None:
    """The map is a bounded now; this is the record behind it.

    Two boundaries, and they are the same two the snapshot has: the Owner comes
    from the session, and the page boundary belongs to the Host. The cursor is
    called a cursor rather than a timestamp on purpose — it is one today and the
    Host must stay free to change that, which the contract gate enforces by
    refusing a `before` parameter here at all.
    """

    _stub_controller(monkeypatch)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        headers = await _authenticate(client)
        first = await client.get(_ACTIVITIES, headers=headers)
        second = await client.get(
            _ACTIVITIES,
            params={"cursor": "opaque-1", "owner_id": "owner-somebody-else"},
            headers=headers,
        )

    assert first.status_code == 200
    assert first.json()["coverage"] == "owner-interactions"
    assert second.status_code == 200
    # The session's Owner both times, whatever the caller asked for; and the
    # cursor passed through untouched.
    assert backend.paged == [("owner-1", None), ("owner-1", "opaque-1")]


async def test_a_history_nobody_can_read_refuses_in_the_usual_shape(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend(fails=True)))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        headers = await _authenticate(client)
        answer = await client.get(_ACTIVITIES, headers=headers)

    assert answer.status_code == 503
    body = answer.json()
    assert "detail" in body or "refusal" in body


async def test_without_a_session_there_is_no_history(tmp_path: Path, monkeypatch) -> None:
    _stub_controller(monkeypatch)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        answer = await client.get(_ACTIVITIES)

    assert answer.status_code == 401
    assert backend.paged == []
