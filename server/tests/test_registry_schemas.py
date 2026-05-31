"""Tests for ``app.registry.schemas`` — the wire shapes for Phase 29.

Most fields are plain Pydantic and don't need tests, but we DO want to
pin down:

  1. The ID validation regex (used identically across Tenant / Template /
     User / Agent — they must all reject the same garbage and accept the
     same valid characters).
  2. The few cross-field invariants (knob overlay value bounds, the
     ConsolidatorConfig sane defaults, the immutability declarations
     baked into UpdateXRequest models).

Bucket constants and key helpers get a smoke test too — easy to typo
and easy to verify.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from eidolon_admin_server.app.registry import (
    ALL_BUCKETS,
    DEVICE_BINDINGS_BUCKET,
    TENANTS_BUCKET,
    device_binding_key,
    tenant_key,
)
from eidolon_admin_server.app.registry.schemas import (
    AgentRef,
    BindDeviceRequest,
    ConsolidatorConfig,
    CreateAgentRequest,
    CreateTemplateRequest,
    CreateTenantRequest,
    CreateUserRequest,
    DeviceView,
    ForkTemplateRequest,
    KnobOverlay,
    ResolvedContext,
    TenantSpec,
    UserSpec,
)


# ---- bucket / key smoke -----------------------------------------------------


def test_buckets_have_distinct_names() -> None:
    """Two buckets, different names — would silently overwrite otherwise."""
    names = [b.name for b in ALL_BUCKETS]
    assert len(names) == len(set(names)), f"duplicate bucket names: {names}"
    assert TENANTS_BUCKET.name == "eidolon_admin_tenants"
    assert DEVICE_BINDINGS_BUCKET.name == "eidolon_admin_device_bindings"


def test_key_helpers_use_dot_prefix() -> None:
    """NATS list_keys(prefix=...) relies on the dot delimiter."""
    assert tenant_key("default") == "tenant.default"
    assert device_binding_key("esp32-foo") == "device.esp32-foo"


# ---- ID validation (shared regex across entities) ---------------------------


@pytest.mark.parametrize(
    "good_id",
    ["default", "alice", "user_1", "Test-Tenant", "a", "x" * 64, "MIX_ed-19"],
)
def test_id_validators_accept_valid_ids(good_id: str) -> None:
    """All four ID-bearing creates must accept the same valid charset."""
    now = datetime.now(timezone.utc)
    # Tenant
    CreateTenantRequest(tenant_id=good_id, display_name="x")
    # Template uses tenant_id + template_id
    CreateTemplateRequest(
        template_id=good_id, tenant_id="default", display_name="x", yaml_body="a: 1"
    )
    # User
    CreateUserRequest(user_id=good_id, display_name="x")
    # Agent (user_id + template_id)
    CreateAgentRequest(user_id=good_id, template_id="caretaker_jiezhi")


@pytest.mark.parametrize(
    "bad_id",
    [
        "",                # empty
        "with space",      # space
        "has.dot",         # dot — reserved for NATS key separator
        "has/slash",       # slash
        "中文",             # non-ASCII
        "x" * 65,          # too long
        "with$dollar",     # special
    ],
)
def test_id_validators_reject_bad_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        CreateTenantRequest(tenant_id=bad_id, display_name="x")


# ---- ConsolidatorConfig defaults --------------------------------------------


def test_consolidator_defaults_match_memory_project() -> None:
    """Keep in sync with memory's DefaultConsolidatorConfig.

    These four values are the contract — if memory ever changes its
    defaults we need to bump these explicitly to keep admin's UI
    consistent.
    """
    c = ConsolidatorConfig()
    assert c.enabled is True
    assert c.interval_hours == 6.0
    assert c.window_days == 30
    assert c.min_drawers == 3
    assert c.min_confidence == 0.6


def test_consolidator_rejects_non_positive_interval() -> None:
    with pytest.raises(ValidationError):
        ConsolidatorConfig(interval_hours=0)
    with pytest.raises(ValidationError):
        ConsolidatorConfig(interval_hours=-1)


# ---- KnobOverlay bounds -----------------------------------------------------


def test_knob_overlay_accepts_values_in_unit_range() -> None:
    KnobOverlay(root={"warmth": 0.0, "formality": 0.5, "humor": 1.0})


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
def test_knob_overlay_rejects_out_of_range(bad: float) -> None:
    with pytest.raises(ValidationError):
        KnobOverlay(root={"warmth": bad})


# ---- CreateAgentRequest set_active default ---------------------------------


def test_create_agent_set_active_defaults_true() -> None:
    """Newly-created agent should become the user's active default by default —
    operator can opt out, but the common case is "I created it, use it".
    """
    req = CreateAgentRequest(user_id="default", template_id="caretaker_jiezhi")
    assert req.set_active is True


# ---- ForkTemplateRequest validation -----------------------------------------


def test_fork_template_request_validates_both_ids() -> None:
    """Fork takes a new template id AND a target tenant — both must be valid."""
    ForkTemplateRequest(
        new_template_id="caretaker_custom_v1",
        target_tenant_id="default",
        new_display_name="My Caretaker",
    )
    with pytest.raises(ValidationError):
        ForkTemplateRequest(
            new_template_id="bad space",
            target_tenant_id="default",
            new_display_name="x",
        )
    with pytest.raises(ValidationError):
        ForkTemplateRequest(
            new_template_id="ok",
            target_tenant_id="bad/slash",
            new_display_name="x",
        )


# ---- ResolvedContext shape --------------------------------------------------


def test_resolved_context_requires_runtime_essentials() -> None:
    """The channel/livekit caller depends on these being present.

    If we ever drop a field, channel code that hardcodes the name
    breaks loudly — exactly what we want.
    """
    ctx = ResolvedContext(
        tenant_id="default",
        user_id="alice",
        agent_id="ag_abc",
        template_id="caretaker_jiezhi",
        template_revision=1,
        memory_mcp_url="http://127.0.0.1:8030/mcp",
        soul_preview="# you are ...",
    )
    assert ctx.tenant_id == "default"
    assert ctx.memory_mcp_url.endswith("/mcp")
    assert ctx.device_id is None  # optional, default


def test_resolved_context_rejects_missing_required() -> None:
    with pytest.raises(ValidationError):
        ResolvedContext(  # missing memory_mcp_url
            tenant_id="default",
            user_id="alice",
            agent_id="ag_abc",
            template_id="caretaker_jiezhi",
            template_revision=1,
            soul_preview="...",
        )


# ---- DeviceView optional binding -------------------------------------------


def test_device_view_unbound_has_none_binding() -> None:
    """approved-but-not-bound: binding is None, resolved fields also None."""
    v = DeviceView(
        device_id="esp32-foo",
        name="Living room",
        kind="esp32",
        approved=True,
        approved_at=datetime.now(timezone.utc),
        last_seen=None,
        status="offline",
    )
    assert v.binding is None
    assert v.resolved_user_id is None
    assert v.resolved_template_id is None


# ---- TenantSpec / UserSpec round trip --------------------------------------


def test_tenant_spec_round_trip_through_json() -> None:
    """Persistence test — Spec → JSON → Spec must be identity (datetimes
    survive ISO formatting)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)  # JSON loses µs
    original = TenantSpec(tenant_id="default", display_name="Default", created_at=now)
    payload = original.model_dump_json()
    restored = TenantSpec.model_validate_json(payload)
    assert restored == original


def test_user_spec_defaults_to_default_tenant() -> None:
    """A bare CreateUserRequest implicitly lands in the default tenant —
    keeps the single-tenant operator experience clean."""
    req = CreateUserRequest(user_id="alice", display_name="Alice")
    assert req.tenant_id == "default"
