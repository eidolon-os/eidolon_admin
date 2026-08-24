"""The Owner-scoped Mission Control projection, held to the shared contract.

The schemas and golden payloads live in the SDK
(`eidolon_sdk/contracts/local_api/v1/`) because both this producer and the Dart
consumer are written against them. This validates what this boundary actually
emits against that schema, so the two cannot drift apart in the one direction a
type checker never catches: a field that is legal Python and illegal on the wire.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from eidolon_admin_server.app.control_plane.contracts import OwnerInventory
from eidolon_admin_server.local_api.host_services import (
    LocalHostServiceInventoryView,
)
from eidolon_admin_server.local_api.mission_control import (
    MissionControlCompanion,
    owner_mission_control_view,
)


def _sdk_contracts() -> Path | None:
    """The contract, from the SDK checkout beside this one."""

    root = Path(__file__).resolve()
    for _ in range(6):
        root = root.parent
        candidate = root / "eidolon_sdk/contracts"
        if candidate.is_dir():
            return candidate
    return None


def _validator() -> jsonschema.Draft202012Validator | None:
    contracts = _sdk_contracts()
    if contracts is None:
        return None
    paths = [
        contracts / "audit/envelope.schema.json",
        contracts / "local_api/v1/mission-control-event.schema.json",
        contracts / "local_api/v1/mission-control-snapshot.schema.json",
    ]
    resources = []
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    return jsonschema.Draft202012Validator(
        json.loads(paths[-1].read_text(encoding="utf-8")),
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )


def _inventory(*, owner_id: str = "owner-1", hub_ok: bool = True) -> OwnerInventory:
    now = datetime(2026, 8, 24, 5, 16, tzinfo=UTC)
    manifest = {
        "schema_version": 1,
        "title": "Box",
        "properties": [],
        "actions": [],
        "events": [],
        "media": [],
    }
    return OwnerInventory.model_validate(
        {
            "owner_id": owner_id,
            "degraded": not hub_ok,
            "hub": (
                {"state": "ok", "latency_ms": 12.0, "failure": None}
                if hub_ok
                else {
                    "state": "error",
                    "latency_ms": 2000.0,
                    "failure": {
                        "authority": "hub",
                        "kind": "unavailable",
                        "detail": "Hub 没有回应",
                        "upstream_status": None,
                        "retryable": True,
                    },
                }
            ),
            "kernel": {"state": "ok", "latency_ms": 8.0, "failure": None},
            "devices": [
                {
                    "operation": "device.directory-entry",
                    "device_id": "device-bound",
                    "owner_scope": owner_id,
                    "display_name": "客厅音箱",
                    "device_kind": "esp32-s3",
                    "manifest": manifest,
                    "manifest_revision": "rev-1",
                    "lifecycle_state": "approved",
                    "enrolled_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "device_ref": None,
                }
            ],
            "mounts": [
                {
                    "operation": "kernel.device-mount",
                    "device_id": "device-bound",
                    "owner_id": owner_id,
                    "device_ref": {
                        "device_instance_id": "device-bound",
                        "owner_domain_id": owner_id,
                        "owner_domain_generation": 1,
                        "claim_generation": 1,
                        "trust_epoch": 1,
                    },
                    "attached_companion_id": "companion-1",
                    "revision": 3,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "request_id": "internal",
                    "fingerprint": "sha256:" + "0" * 64,
                    "active": True,
                },
                {
                    "operation": "kernel.device-mount",
                    "device_id": "device-removed",
                    "owner_id": owner_id,
                    "device_ref": {
                        "device_instance_id": "device-removed",
                        "owner_domain_id": owner_id,
                        "owner_domain_generation": 1,
                        "claim_generation": 1,
                        "trust_epoch": 1,
                    },
                    "attached_companion_id": None,
                    "revision": 5,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "request_id": "internal",
                    "fingerprint": "sha256:" + "1" * 64,
                    "active": False,
                },
            ],
        }
    )


def _services(*, unknown: bool = True) -> LocalHostServiceInventoryView:
    now = datetime(2026, 8, 24, 5, 16, tzinfo=UTC)
    return LocalHostServiceInventoryView.model_validate(
        {
            "services": [
                {
                    "service_id": "hub",
                    "required": True,
                    "enabled": True,
                    "revision": 4,
                    "runtime_state": "ready",
                    "detail": None,
                    "observed_at": now.isoformat(),
                },
                {
                    "service_id": "mementos",
                    "required": False,
                    "enabled": True,
                    "revision": 2,
                    "runtime_state": "unknown" if unknown else "failed",
                    "detail": "没有探测到",
                    "observed_at": now.isoformat(),
                },
            ]
        }
    )


def _companion() -> MissionControlCompanion:
    return MissionControlCompanion(
        companion_id="companion-1",
        display_name="砚舟",
        is_primary=True,
        lifecycle_state="active",
        genome_id="genome-1",
        memory_realm_id="realm-1",
    )


def _view(**overrides):
    kwargs = {
        "bound_owner_id": "owner-1",
        "owner_display_name": "沈亦",
        "companion": _companion(),
        "inventory": _inventory(),
        "services": _services(),
    }
    kwargs.update(overrides)
    return owner_mission_control_view(**kwargs)


def test_snapshot_matches_the_shared_schema() -> None:
    validator = _validator()
    if validator is None:
        pytest.skip("eidolon_sdk checkout is not present")
    validator.validate(_view().model_dump(mode="json"))


def test_every_failure_costs_one_lane_and_not_the_response() -> None:
    """One authority down must not black out the ones that answered."""

    view = _view(
        companion=None,
        companion_detail="读不到伙伴：Data 不可用",
        services=None,
        services_detail="读不到主机在跑什么",
    )

    assert view.companions.state == "unavailable"
    assert "Data 不可用" in view.companions.detail
    assert view.services.state == "unavailable"
    # Devices and owner read fine, and still say so.
    assert view.owner.state == "ok"
    assert view.devices.items
    validator = _validator()
    if validator is not None:
        validator.validate(view.model_dump(mode="json"))


def test_presence_is_never_invented() -> None:
    """A mount proves membership; a directory entry proves a name.

    Neither is a presence, and this boundary has no port to one — so every
    device says nobody answered, and the client renders unprobed rather than
    offline.
    """

    device = _view().devices.items[0]
    assert device.presence.state == "unknown"
    assert device.presence.source == "none"
    assert device.presence.observed_at is None
    # And the lane is not `ok`: a partial read must not look like a full one.
    assert _view().devices.state == "degraded"
    assert "在场没有权威回答" in _view().devices.detail


def test_inactive_mounts_are_not_bodies() -> None:
    """Kernel keeps a removed device's mount for the next admission's revision.

    It is not a membership, so it is not a body on this screen.
    """

    ids = [device.device_id for device in _view().devices.items]
    assert ids == ["device-bound"]


def test_unprobed_service_is_unknown_not_healthy() -> None:
    services = {row.service_id: row for row in _view().services.items}
    assert services["hub"].online is True
    assert services["hub"].checked is True
    # `unknown` means nobody could tell. It must not arrive as healthy.
    assert services["mementos"].checked is False
    assert services["mementos"].online is False

    failed = {
        row.service_id: row
        for row in _view(services=_services(unknown=False)).services.items
    }
    assert failed["mementos"].checked is True
    assert failed["mementos"].online is False


def test_an_authority_failure_is_quoted_in_the_words_it_used() -> None:
    view = _view(inventory=_inventory(hub_ok=False))
    assert "Hub：unavailable" in view.devices.detail
    assert "Hub 没有回应" in view.devices.detail
    # The lane still carries what Kernel did answer.
    assert view.devices.items


def test_lanes_with_no_producer_say_which_capability_is_missing() -> None:
    view = _view()
    for lane in (view.activities, view.turns, view.jobs, view.events, view.memory):
        assert lane.state == "unavailable"
        assert lane.detail, "an unavailable lane with no detail is unactionable"
    assert "活动投影" in view.activities.detail
    assert "事件游标" in view.events.detail
    # No events read, so no cursor: one invented here would send a client back
    # to a position that never existed.
    assert view.cursor is None


def test_owner_name_missing_is_degraded_not_absent() -> None:
    view = _view(owner_display_name=None)
    assert view.owner.state == "degraded"
    assert view.owner.value is not None
    assert view.owner.value.owner_id == "owner-1"
    # Left empty rather than filled with the identifier.
    assert view.owner.value.display_name == ""


def test_only_the_primary_companion_is_reachable_and_it_says_so() -> None:
    view = _view()
    assert view.companions.state == "degraded"
    assert "只提供主 Companion" in view.companions.detail
    assert len(view.companions.items) == 1
    assert view.companions.items[0].is_primary is True
