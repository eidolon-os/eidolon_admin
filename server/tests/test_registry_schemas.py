from __future__ import annotations

import pytest
from pydantic import ValidationError

from eidolon_admin_server.app.registry.schemas import (
    ResolvedContext,
    ResolveDeviceResponse,
)


def test_resolved_context_requires_runtime_identity_tuple() -> None:
    ctx = ResolvedContext(
        owner_id="owner-1",
        companion_id="companion-1",
        device_id="dev-1",
        memory_realm_id="realm-1",
        genome_id="genome-1",
    )

    assert ctx.owner_id == "owner-1"
    assert ctx.companion_id == "companion-1"
    assert ctx.device_id == "dev-1"
    assert ctx.memory_realm_id == "realm-1"
    assert ctx.genome_id == "genome-1"


def test_resolved_context_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        ResolvedContext(
            owner_id="owner-1",
            companion_id="companion-1",
            device_id="dev-1",
            genome_id="genome-1",
        )


def test_resolve_device_response_wraps_context() -> None:
    response = ResolveDeviceResponse(
        context=ResolvedContext(
            owner_id="owner-1",
            companion_id="companion-1",
            device_id="dev-1",
            memory_realm_id="realm-1",
            genome_id="genome-1",
        )
    )

    assert response.context.companion_id == "companion-1"
