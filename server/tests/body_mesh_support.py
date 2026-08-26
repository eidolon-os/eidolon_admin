"""One place that builds the Kernel Body documents these tests stand in for.

Written once rather than in each test file, for the reason the plan keeps
running into: a fake producer spelled slightly differently in five places is how
tests come to agree with a bug. If the Kernel's endpoint document changes shape,
exactly one thing here stops matching it — and the consumer contract tests that
compare this shape to the producer's own schema are what catch that.
"""

from __future__ import annotations

from typing import Any


def assignment_document(
    *,
    device_id: str,
    owner_id: str,
    companion_id: str | None,
    revision: int = 1,
    generation: int | None = None,
    selection_provenance: str | None = None,
    change_reason: str | None = None,
    effective: bool = True,
    updated_at: str = "2026-08-09T08:10:00Z",
) -> dict[str, Any]:
    """One assignment, as Kernel publishes it.

    ``effective`` is separate from ``companion_id`` on purpose: a Body whose
    device is gone keeps naming its Companion while the status reports nobody in
    force, and every consumer has to read the second rather than the first.
    """

    if selection_provenance is None:
        selection_provenance = "user_selected" if companion_id else "user_cleared"
    in_force = companion_id if (companion_id and effective) else None
    return {
        "operation": "kernel.body-assignment",
        "assignment_id": f"assignment:{device_id}:body",
        "body_endpoint_id": f"{device_id}:body",
        "device_id": device_id,
        "endpoint_id": "body",
        "owner_id": owner_id,
        "companion_id": companion_id,
        "selection_provenance": selection_provenance,
        "change_reason": change_reason,
        "mode": "default",
        "policy_refs": [],
        "revision": revision,
        "generation": generation if generation is not None else revision,
        "updated_at": updated_at,
        "status": {
            "observed_generation": generation if generation is not None else revision,
            "effective_companion_id": in_force,
            "conditions": (
                ["Realized"] if in_force else ([] if effective else ["CapabilityMissing"])
            ),
        },
    }


def endpoint_document(
    *,
    device_id: str,
    owner_id: str,
    companion_id: str | None = None,
    assignment_revision: int = 1,
    mount_revision: int = 1,
    present: bool = True,
    owner_domain_id: str | None = None,
    claim_generation: int = 1,
    trust_epoch: int = 1,
    owner_domain_generation: int = 1,
    selection_provenance: str | None = None,
    assigned: bool = True,
    updated_at: str = "2026-08-09T08:10:00Z",
) -> dict[str, Any]:
    """One Body, as Kernel publishes it.

    ``assigned=False`` is a Body nobody has decided about, which is not the same
    as one that was cleared: the first has no assignment row at all.
    """

    return {
        "operation": "kernel.body-endpoint",
        "body_endpoint_id": f"{device_id}:body",
        "device_id": device_id,
        "owner_id": owner_id,
        "endpoint_id": "body",
        "device_ref": {
            "device_instance_id": device_id,
            "owner_domain_id": owner_domain_id or owner_id,
            "owner_domain_generation": owner_domain_generation,
            "claim_generation": claim_generation,
            "trust_epoch": trust_epoch,
        },
        "mount_revision": mount_revision,
        "roles": ["body"],
        "assignment_policy": "optional",
        "risk_class": "safe",
        "concurrency": "exclusive",
        "source": "derived",
        "present": present,
        "assignment": (
            assignment_document(
                device_id=device_id,
                owner_id=owner_id,
                companion_id=companion_id,
                revision=assignment_revision,
                selection_provenance=selection_provenance,
                effective=present,
                updated_at=updated_at,
            )
            if assigned
            else None
        ),
    }


def endpoint_page(*documents: dict[str, Any]) -> dict[str, Any]:
    return {"operation": "kernel.body-endpoint-page", "endpoints": list(documents)}
