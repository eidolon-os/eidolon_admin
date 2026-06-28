"""Pydantic wire shapes for the five registry entities.

One file per entity. Each file follows the same internal structure:
    1. Identifier / validation helpers (regex constants etc.).
    2. The ``Spec`` model — the canonical persisted shape.
    3. ``CreateXRequest`` / ``UpdateXRequest`` — incoming payloads.
    4. ``XView`` — composed view that admin returns (Spec + cross-cutting).
    5. List / mutation response envelopes.

Re-exported here for terse imports::

    from ..registry.schemas import TenantSpec, CreateTenantRequest
"""
from .agent import (
    AgentDetail,
    AgentListResponse,
    AgentRef,
    CreateAgentRequest,
    KnobOverlay,
    UpdateAgentKnobsRequest,
    UpdateAgentSoulRequest,
)
from .device import (
    BindDeviceRequest,
    DeviceBinding,
    DeviceListResponse,
    DeviceView,
    LiveKitRuntimeStatus,
    UnbindDeviceResponse,
)
from .resolve import ResolvedContext, ResolveDeviceResponse, ResolveUserResponse
from .tenant import (
    CreateTenantRequest,
    TenantListResponse,
    TenantSpec,
    UpdateTenantRequest,
)
from .template import (
    CreateTemplateRequest,
    ForkTemplateRequest,
    TemplateDetail,
    TemplateListResponse,
    TemplateRef,
    TemplateSource,
    UpdateTemplateRequest,
)
from .user import (
    ConsolidatorConfig,
    CreateUserRequest,
    SetActiveAgentRequest,
    UpdateUserRequest,
    UserHealth,
    UserListResponse,
    UserSpec,
    UserView,
)

__all__ = [
    # tenant
    "TenantSpec",
    "CreateTenantRequest",
    "UpdateTenantRequest",
    "TenantListResponse",
    # template
    "TemplateRef",
    "TemplateDetail",
    "TemplateSource",
    "CreateTemplateRequest",
    "UpdateTemplateRequest",
    "ForkTemplateRequest",
    "TemplateListResponse",
    # user
    "UserSpec",
    "UserView",
    "UserHealth",
    "ConsolidatorConfig",
    "CreateUserRequest",
    "UpdateUserRequest",
    "SetActiveAgentRequest",
    "UserListResponse",
    # agent
    "AgentRef",
    "AgentDetail",
    "KnobOverlay",
    "CreateAgentRequest",
    "UpdateAgentKnobsRequest",
    "UpdateAgentSoulRequest",
    "AgentListResponse",
    # device
    "DeviceBinding",
    "DeviceView",
    "LiveKitRuntimeStatus",
    "BindDeviceRequest",
    "UnbindDeviceResponse",
    "DeviceListResponse",
    # resolve
    "ResolvedContext",
    "ResolveDeviceResponse",
    "ResolveUserResponse",
]
