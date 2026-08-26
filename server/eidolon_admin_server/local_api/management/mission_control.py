"""The Owner's devices lane: who exists, joined with who is present.

Two authorities, and neither can answer for the other:

* **existence and identity** — which bodies are this Owner's, what they are
  called, and which Eidolon each answers as — is Claims (Hub) joined with mounts
  (Kernel). This plane already reads it on the Owner's behalf for their device
  list, from a Controller session; the Admin process that composes Mission
  Control holds no credential for either, so it cannot;
* **presence** — which of them are actually reachable right now — is the
  owner-isolated runtime blackboard, which is what the Mission Control
  composition can see.

Before this join the star map drew only the bodies the blackboard knew about, so
the same Owner's device list and their map disagreed about how many bodies they
have. Joining here rather than in the Admin process keeps each fact with its
authority and keeps the credential where it belongs — the same reason `/home`
composes here.

**Existence is never read as presence.** An active Claim and a Kernel mount both
say a device is *known*; neither says it is switched on, and the device list says
so out loud (`online="unknown"`, "这台主机没有任何东西在观测设备是否开着"). A row
this join adds from the inventory therefore carries `unknown` presence from a
source of `none` — not `offline`, which would tell someone their speaker is off
when the truth is that nobody with standing said.
"""

from __future__ import annotations

from typing import Any

#: What a body's role reads as, mirroring the words the Mission Control
#: composition uses for the same three cases (`_device_role`). Guard is absent
#: on purpose: a Guard binding is what makes a body a sentry, the Guard runtime
#: does not exist, and this plane has no binding to read.
_ROLE_ANSWERING = ("对话身体", "persona")
_ROLE_UNBOUND = ("未绑定", "unbound")


def join_owner_devices(
    snapshot: dict[str, Any],
    *,
    inventory: Any | None,
    failure: str = "",
) -> dict[str, Any]:
    """Return ``snapshot`` with its devices lane joined to the Owner's inventory.

    ``inventory`` is the Owner's device list (a ``DevicesView``); ``failure`` is
    why there is none, when there is none. Both absent and broken are the same
    shape here and a different lane state: a lane that could not read existence
    but could read presence is partly known, which is what ``degraded`` is for.
    """

    lane = snapshot.get("devices")
    if not isinstance(lane, dict):
        # Not a shape this function understands. Handing it back untouched beats
        # asserting a contract the caller has already broken.
        return snapshot

    presence_readable = lane.get("state") != "unavailable"
    rows: list[dict[str, Any]] = [
        dict(row) for row in lane.get("items", []) if isinstance(row, dict)
    ]
    by_id = {row.get("device_id"): row for row in rows}

    devices = list(getattr(inventory, "devices", []) or []) if inventory is not None else []
    for device in devices:
        device_id = getattr(device, "device_id", "") or ""
        if not device_id:
            continue
        existing = by_id.get(device_id)
        if existing is not None:
            _fill(existing, device)
            continue
        rows.append(_row(device))

    state, detail = _health(
        presence_readable=presence_readable,
        presence_detail=str(lane.get("detail") or ""),
        existence_readable=inventory is not None,
        existence_detail=failure,
        degraded=lane.get("state") == "degraded",
    )
    joined = {
        **lane,
        "state": state,
        "detail": detail,
        "items": rows if state != "unavailable" else [],
    }
    return {**snapshot, "devices": joined}


def _row(device: Any) -> dict[str, Any]:
    """A body the blackboard has never seen, from the inventory alone."""

    companion_id = getattr(device, "answers_as_companion_id", None)
    role, role_kind = _ROLE_ANSWERING if companion_id else _ROLE_UNBOUND
    return {
        "device_id": getattr(device, "device_id", ""),
        "display_name": getattr(device, "label", "") or getattr(device, "device_id", ""),
        "device_kind": getattr(device, "kind", "") or "",
        "role": role,
        "role_kind": role_kind,
        "companion_id": companion_id,
        # The inventory knows what a body is for, not what it can do right now.
        "capabilities": [],
        "presence": {
            # Known to exist, and unobserved. Two different facts, and this is
            # the second one.
            "state": "unknown",
            "source": "none",
            "observed_at": None,
        },
    }


def _fill(row: dict[str, Any], device: Any) -> None:
    """Give a present body the name and binding the inventory holds.

    Only what is missing, and never presence: the blackboard is the presence
    authority and it already answered for this row.
    """

    label = getattr(device, "label", "") or ""
    if label and not row.get("display_name"):
        row["display_name"] = label
    kind = getattr(device, "kind", "") or ""
    if kind and not row.get("device_kind"):
        row["device_kind"] = kind
    companion_id = getattr(device, "answers_as_companion_id", None)
    if companion_id and not row.get("companion_id"):
        row["companion_id"] = companion_id
        if row.get("role_kind") in (None, "", "unbound"):
            row["role"], row["role_kind"] = _ROLE_ANSWERING


def _health(
    *,
    presence_readable: bool,
    presence_detail: str,
    existence_readable: bool,
    existence_detail: str,
    degraded: bool,
) -> tuple[str, str]:
    reasons = [
        text
        for text in (
            presence_detail if not presence_readable else "",
            existence_detail if not existence_readable else "",
        )
        if text
    ]
    if not presence_readable and not existence_readable:
        return "unavailable", "；".join(reasons) or "身体既读不到在场，也读不到清单"
    if not presence_readable or not existence_readable:
        return "degraded", "；".join(reasons) or "这条 lane 只读到了一半"
    # Both answered. A `degraded` from upstream survives: it was said about
    # something else in this lane, and this join has no standing to clear it.
    return ("degraded" if degraded else "ok"), presence_detail


__all__ = ["join_owner_devices"]
