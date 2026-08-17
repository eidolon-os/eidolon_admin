"""What has happened on this Host lately, as its Owner reads it.

The Hub has been keeping a record of every device that knocked, was accepted
or was taken away, and nobody could see it. An Owner spent a day unable to
tell whether a device had arrived while the answer sat in a table three
services away.

This is that record, and only that record. It is named `device-lifecycle`
rather than `activity` in its coverage field because that is all it covers:
this Host has no presence signal, no heartbeat and no runtime telemetry, so a
screen built on this must not imply it is showing everything that happened.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..app.control_plane.contracts import HubDevice, HubDeviceEvent, OwnerDeviceHistory

#: What the Hub calls the things it records, and what those mean to a person.
#: An event type this Host does not recognise is still shown — it happened,
#: and dropping it would turn an unfamiliar act into a quiet stretch.
_KINDS: dict[str, str] = {
    "eidolon.device.enrolled.v1": "device-knocked",
    "eidolon.device.approved.v1": "device-accepted",
    "eidolon.device.revoked.v1": "device-removed",
}

#: How the Hub names whoever acted. A device enrolling speaks for itself and
#: has no standing yet; the Local API acts because a person told it to.
_DEVICE_PRINCIPAL_PREFIX = "untrusted-device:"
_OWNER_PRINCIPAL_PREFIX = "eidolon-local-api/"


class LocalMomentView(BaseModel):
    """One thing that happened, said in terms a person can act on.

    The device is carried by name. Its identifier travels too, because a
    screen showing several boards of the same model needs something to tell
    them apart in a technical corner — but the identifier is never the answer
    to "what happened", and a Host that cannot name the device says so with an
    empty name rather than filling the gap with the identifier.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    kind: Literal[
        "device-knocked",
        "device-accepted",
        "device-removed",
        "other",
    ]
    actor: Literal["owner", "device", "host"]
    device_id: str = Field(min_length=1, max_length=255)
    device_name: str = Field(default="", max_length=128)
    device_kind: str = Field(default="", max_length=96)
    #: Why, when the record gives a reason. Only removals carry one today.
    reason: str = Field(default="", max_length=256)
    #: What the Hub called this, for the technical corner of a screen.
    event_type: str = Field(min_length=1, max_length=255)


class LocalActivityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    coverage: Literal["device-lifecycle"] = "device-lifecycle"
    moments: tuple[LocalMomentView, ...] = Field(default=(), max_length=200)


def owner_activity_view(history: OwnerDeviceHistory) -> LocalActivityView:
    """Turn a Hub ledger into moments, newest first."""

    named: dict[str, HubDevice] = {entry.device_id: entry for entry in history.devices}
    return LocalActivityView(
        moments=tuple(_moment(event, named.get(event.device_id)) for event in history.events)
    )


def _moment(event: HubDeviceEvent, device: HubDevice | None) -> LocalMomentView:
    reason = event.data.get("reason")
    return LocalMomentView(
        event_id=event.event_id,
        occurred_at=event.occurred_at,
        kind=_KINDS.get(event.event_type, "other"),
        actor=_actor(event.principal_id),
        device_id=event.device_id,
        device_name=device.display_name if device else "",
        device_kind=device.device_kind if device else "",
        reason=reason if isinstance(reason, str) else "",
        event_type=event.event_type,
    )


def _actor(principal_id: str) -> Literal["owner", "device", "host"]:
    if principal_id.startswith(_DEVICE_PRINCIPAL_PREFIX):
        return "device"
    if principal_id.startswith(_OWNER_PRINCIPAL_PREFIX):
        # A Controller acted, and a Controller only ever acts because the
        # person holding it said so. Which phone it was is not what happened.
        return "owner"
    return "host"
