"""PersonaTemplate — character / persona authoring resource.

Business implementation lives in eidolon_agent. Admin's contract is a
**proxy + composed view**: list/get/create/update/delete/fork all
translate to REST calls against agent's ``/api/admin/templates`` surface
(see docs/architecture/phase-29-five-entity-model.md §3).

Two kinds:
    builtin — ship with the agent project's source tree (yaml files).
              Read-only via the API. Operator can ``fork`` one to make
              a custom copy in their tenant.
    custom  — created by the operator, stored by the agent project
              (e.g. its SQLite or a NATS KV bucket it owns). Editable
              and deletable.

Versioning:
    Each template carries an integer ``revision``. When a custom
    template is edited, the revision bumps. Existing agents created
    from an older revision keep working (their soul.md is rendered
    once and stored in NATS); the UI surfaces "N agents on older
    revision" so the operator can decide to re-render explicitly.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TemplateSource = Literal["builtin", "custom"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_id(value: str, *, field_name: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(
            f"{field_name} must be 1-64 chars of [A-Za-z0-9_-]; got {value!r}"
        )
    return value


class TemplateRef(BaseModel):
    """Lightweight summary for list views.

    The full template document can be 5-50 KB of structured yaml; the
    list page never needs it. Detail page fetches ``TemplateDetail``
    separately.
    """

    template_id: str
    tenant_id: str  # builtin templates surface as tenant_id="default"
    source: TemplateSource
    revision: int = Field(..., ge=1)
    display_name: str
    archetype: str  # e.g. "caretaker", "playful" — for at-a-glance browsing
    updated_at: datetime


class TemplateDetail(BaseModel):
    """Full document. Returned by GET /api/templates/{id} only."""

    ref: TemplateRef
    # The raw yaml content as a string. Frontend uses a YAML editor; admin
    # validates server-side against agent's schema before persisting.
    yaml_body: str
    # Refcount: how many existing agents were rendered from this template.
    # Drives the delete-confirmation UI ("3 agents reference this — block").
    agent_refcount: int = Field(..., ge=0)


class CreateTemplateRequest(BaseModel):
    """POST /api/templates body — creates a custom template.

    To create from scratch, send the full ``yaml_body``. To clone from
    a builtin, use the ``fork`` endpoint instead.
    """

    template_id: str = Field(..., min_length=1, max_length=64)
    tenant_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    yaml_body: str = Field(..., min_length=1)

    @field_validator("template_id")
    @classmethod
    def _check_template_id(cls, v: str) -> str:
        return _validate_id(v, field_name="template_id")

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant_id(cls, v: str) -> str:
        return _validate_id(v, field_name="tenant_id")


class UpdateTemplateRequest(BaseModel):
    """PUT /api/templates/{id} body. Custom templates only."""

    display_name: str | None = Field(None, min_length=1, max_length=128)
    yaml_body: str | None = Field(None, min_length=1)


class ForkTemplateRequest(BaseModel):
    """POST /api/templates/{id}/fork body — clone a builtin into a custom."""

    new_template_id: str = Field(..., min_length=1, max_length=64)
    target_tenant_id: str = Field(..., min_length=1, max_length=64)
    new_display_name: str = Field(..., min_length=1, max_length=128)

    @field_validator("new_template_id")
    @classmethod
    def _check_new_id(cls, v: str) -> str:
        return _validate_id(v, field_name="new_template_id")

    @field_validator("target_tenant_id")
    @classmethod
    def _check_tenant(cls, v: str) -> str:
        return _validate_id(v, field_name="target_tenant_id")


class TemplateListResponse(BaseModel):
    templates: list[TemplateRef]
    # Whether the upstream (agent project) responded. If False, the list is
    # empty and admin should banner "agent service unavailable" rather than
    # pretending no templates exist.
    upstream_available: bool
