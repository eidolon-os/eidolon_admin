"""Single-entry onboarding API for ordinary Eidolon users."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from eidolon_data import DataStore
from eidolon_data.services import OwnerWorkspaceError
from eidolon_sdk.biz.persona import (
    PersonaAuthoringDraft,
    build_persona_genome_from_draft,
    persona_genome_to_json,
)
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..data.schemas import (
    CompanionView,
    DeviceView,
    MemoryRealmView,
    OwnerView,
    PersonaGenomeView,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
COMPANION_TYPE_MASTER = "master"
COMPANION_TYPE_SLAVE = "slave"
MISSING_OWNER = "owner"
MISSING_MASTER = "master_companion"
MISSING_GENOME = "current_genome"
MISSING_MEMORY_REALM = "memory_realm"
MISSING_WEB_DEVICE = "web_device"
SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


class LaunchIdentity(BaseModel):
    owner_id: str
    companion_id: str
    device_id: str
    launch_url: str


class OnboardingState(BaseModel):
    owners: list[OwnerView] = Field(default_factory=list)
    owner: OwnerView | None = None
    companions: list[CompanionView] = Field(default_factory=list)
    master_companion: CompanionView | None = None
    web_device: DeviceView | None = None
    ready: bool = False
    master_ready: bool = False
    repair_required: bool = False
    missing: list[str] = Field(default_factory=list)
    launch_identity: LaunchIdentity | None = None


class OnboardingInitializeRequest(BaseModel):
    owner_id: str | None = None
    owner_display_name: str = ""
    companion_id: str | None = None
    companion_display_name: str = ""
    self_concept: str | None = None
    character_portrait: str | None = None
    relationship_narrative: str | None = None
    voice_portrait: str | None = None
    values: list[str] | None = None
    boundaries: list[str] | None = None
    commitments: list[str] | None = None
    behavior_guidance: list[str] | None = None
    dialogue_examples: list[str] | None = None
    pinned_facts: list[str] | None = None
    safety_boundaries: list[str] | None = None
    owner_profile_json: JsonDict = Field(default_factory=dict)
    owner_settings_json: JsonDict = Field(default_factory=dict)


class OnboardingCompanionCreateRequest(BaseModel):
    owner_id: str | None = None
    companion_id: str | None = None
    companion_display_name: str
    self_concept: str | None = None
    character_portrait: str | None = None
    relationship_narrative: str | None = None
    voice_portrait: str | None = None
    values: list[str] | None = None
    boundaries: list[str] | None = None
    commitments: list[str] | None = None
    behavior_guidance: list[str] | None = None
    dialogue_examples: list[str] | None = None
    pinned_facts: list[str] | None = None
    safety_boundaries: list[str] | None = None
    create_web_device: bool = False


class PersonaAuthoringPreviewRequest(BaseModel):
    companion_display_name: str
    self_concept: str | None = None
    character_portrait: str | None = None
    relationship_narrative: str | None = None
    voice_portrait: str | None = None
    values: list[str] | None = None
    boundaries: list[str] | None = None
    commitments: list[str] | None = None
    behavior_guidance: list[str] | None = None
    dialogue_examples: list[str] | None = None
    pinned_facts: list[str] | None = None
    safety_boundaries: list[str] | None = None


class PersonaAuthoringDraftResponse(BaseModel):
    draft: JsonDict


class PersonaAuthoringPreviewResponse(BaseModel):
    genome: JsonDict


class OnboardingInitializeResponse(BaseModel):
    state: OnboardingState


class OnboardingCompanionCreateResponse(BaseModel):
    companion: CompanionView
    persona_genome: PersonaGenomeView
    memory_realm: MemoryRealmView
    launch_identity: LaunchIdentity | None = None
    state: OnboardingState


class OnboardingLaunchRequest(BaseModel):
    owner_id: str | None = None
    companion_id: str | None = None


class OnboardingLaunchResponse(BaseModel):
    owner_id: str
    companion_id: str
    device_id: str
    launch_url: str


@router.get("/state", response_model=OnboardingState)
async def get_onboarding_state(
    request: Request,
    owner_id: str | None = Query(default=None),
) -> OnboardingState:
    return await _build_state(_store(request), owner_id=owner_id)


@router.get("/persona-authoring/defaults", response_model=PersonaAuthoringDraftResponse)
async def get_persona_authoring_defaults(
    name: str = Query(default="Companion"),
) -> PersonaAuthoringDraftResponse:
    draft = PersonaAuthoringDraft(name=name.strip() or "Companion")
    return PersonaAuthoringDraftResponse(draft=draft.model_dump(mode="json"))


@router.post("/persona-authoring/preview", response_model=PersonaAuthoringPreviewResponse)
async def preview_persona_authoring(
    payload: PersonaAuthoringPreviewRequest,
) -> PersonaAuthoringPreviewResponse:
    genome = build_persona_genome_from_draft(
        _authoring_draft(payload),
        origin="owner_onboarding_preview",
    )
    return PersonaAuthoringPreviewResponse(genome=persona_genome_to_json(genome))


@router.post("/initialize", response_model=OnboardingInitializeResponse)
async def initialize_onboarding(
    payload: OnboardingInitializeRequest,
    request: Request,
) -> OnboardingInitializeResponse:
    store = _store(request)
    owner = await _select_owner(store, payload.owner_id)
    if owner is None:
        owner_id = _owner_id(payload.owner_id, payload.owner_display_name)
        try:
            result = await store.owner_service.create_owner(
                owner_id=owner_id,
                display_name=payload.owner_display_name.strip() or owner_id,
                kind="person",
                profile_json=payload.owner_profile_json,
                settings_json=payload.owner_settings_json,
                actor_type="admin",
                actor_id="onboarding",
            )
            owner = result.owner
        except OwnerWorkspaceError as exc:
            _raise_workspace_error(exc)
        except IntegrityError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "owner already exists") from exc

    companions = [c for c in await store.companions.list_for_owner(owner.owner_id) if c.status == "active"]
    master = _find_master(companions)
    try:
        if master is None and not companions:
            ids = _workspace_ids(
                owner_id=owner.owner_id,
                companion_name=payload.companion_display_name,
                companion_id=payload.companion_id,
                role="master",
            )
            result = await store.workspace_provisioning.provision_workspace(
                owner_id=owner.owner_id,
                companion_id=ids["companion_id"],
                companion_display_name=payload.companion_display_name.strip()
                or f"{owner.display_name or owner.owner_id} Companion",
                companion_profile_json=_companion_profile(payload),
                companion_metadata_json={
                    "source": "owner_onboarding",
                    "companion_type": COMPANION_TYPE_MASTER,
                },
                genome_id=ids["genome_id"],
                genome_source_json={"source_type": "owner_onboarding", "owner_id": owner.owner_id},
                genome_json=_genome_json(payload),
                realm_id=ids["realm_id"],
                actor_type="admin",
                actor_id="onboarding",
                is_master=True,
            )
            master = result.companion
        elif master is None:
            master = await store.workspace_provisioning.promote_to_master(
                owner_id=owner.owner_id,
                companion_id=companions[0].companion_id,
                actor_type="admin",
                actor_id="onboarding",
            )
        else:
            await _ensure_master_ready(store, owner_id=owner.owner_id, master=master, payload=payload)
    except OwnerWorkspaceError as exc:
        _raise_workspace_error(exc)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "workspace already initialized") from exc

    _schedule_memory_supervisor_reconcile(request)
    return OnboardingInitializeResponse(
        state=await _build_state(store, owner_id=owner.owner_id),
    )


@router.post("/companions", response_model=OnboardingCompanionCreateResponse)
async def create_onboarding_companion(
    payload: OnboardingCompanionCreateRequest,
    request: Request,
) -> OnboardingCompanionCreateResponse:
    store = _store(request)
    owner = await _select_owner(store, payload.owner_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found")
    ids = _workspace_ids(
        owner_id=owner.owner_id,
        companion_name=payload.companion_display_name,
        companion_id=payload.companion_id,
        role="slave",
    )
    try:
        result = await store.workspace_provisioning.provision_workspace(
            owner_id=owner.owner_id,
            companion_id=ids["companion_id"],
            companion_display_name=payload.companion_display_name.strip(),
            companion_profile_json=_companion_profile(payload),
            companion_metadata_json={
                "source": "owner_onboarding",
                "companion_type": COMPANION_TYPE_SLAVE,
            },
            genome_id=ids["genome_id"],
            genome_source_json={"source_type": "owner_onboarding", "owner_id": owner.owner_id},
            genome_json=_genome_json(payload),
            realm_id=ids["realm_id"],
            actor_type="admin",
            actor_id="onboarding",
            is_master=False,
        )
        launch_identity = None
        if payload.create_web_device:
            device = await store.workspace_provisioning.ensure_web_body(
                owner_id=owner.owner_id,
                companion_id=result.companion.companion_id,
            )
            launch_identity = _launch_identity(owner.owner_id, result.companion.companion_id, device.device_id)
    except OwnerWorkspaceError as exc:
        _raise_workspace_error(exc)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "companion already exists") from exc

    _schedule_memory_supervisor_reconcile(request)
    return OnboardingCompanionCreateResponse(
        companion=_companion(result.companion),
        persona_genome=_persona_genome(result.persona_genome),
        memory_realm=_memory_realm(result.memory_realm),
        launch_identity=launch_identity,
        state=await _build_state(store, owner_id=owner.owner_id),
    )


@router.post("/launch", response_model=OnboardingLaunchResponse)
async def launch_onboarding_companion(
    payload: OnboardingLaunchRequest,
    request: Request,
) -> OnboardingLaunchResponse:
    store = _store(request)
    owner = await _select_owner(store, payload.owner_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found")

    companion = None
    companions = [c for c in await store.companions.list_for_owner(owner.owner_id) if c.status == "active"]
    if payload.companion_id:
        companion = next((c for c in companions if c.companion_id == payload.companion_id), None)
    else:
        companion = _find_master(companions) or (companions[0] if companions else None)
    if companion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "companion not found")

    try:
        device = await store.workspace_provisioning.ensure_web_body(
            owner_id=owner.owner_id,
            companion_id=companion.companion_id,
        )
    except OwnerWorkspaceError as exc:
        _raise_workspace_error(exc)
    identity = _launch_identity(owner.owner_id, companion.companion_id, device.device_id)
    return OnboardingLaunchResponse(**identity.model_dump())


async def _build_state(store: DataStore, *, owner_id: str | None = None) -> OnboardingState:
    owners = [o for o in await store.owners.list() if o.status == "active"]
    owner = await _select_owner(store, owner_id, owners=owners)
    if owner is None:
        return OnboardingState(
            owners=[_owner(row) for row in owners],
            missing=[MISSING_OWNER],
            repair_required=True,
        )

    companions = [c for c in await store.companions.list_for_owner(owner.owner_id) if c.status == "active"]
    master = _find_master(companions)
    missing: list[str] = []
    web_device = None
    launch_identity = None

    if master is None:
        missing.append(MISSING_MASTER)
    else:
        if not await _has_current_genome(store, master):
            missing.append(MISSING_GENOME)
        if not await _has_active_memory_realm(store, master):
            missing.append(MISSING_MEMORY_REALM)
        web_device = await _web_device(store, master.companion_id)
        if web_device is None:
            missing.append(MISSING_WEB_DEVICE)
        else:
            launch_identity = _launch_identity(owner.owner_id, master.companion_id, web_device.device_id)

    master_ready = not missing
    return OnboardingState(
        owners=[_owner(row) for row in owners],
        owner=_owner(owner),
        companions=[_companion(row) for row in companions],
        master_companion=_companion(master) if master is not None else None,
        web_device=_device(web_device) if web_device is not None else None,
        ready=master_ready,
        master_ready=master_ready,
        repair_required=bool(missing),
        missing=missing,
        launch_identity=launch_identity if master_ready else None,
    )


async def _ensure_master_ready(
    store: DataStore,
    *,
    owner_id: str,
    master: Any,
    payload: OnboardingInitializeRequest,
) -> None:
    if not await _has_current_genome(store, master):
        latest = await store.persona_repo.get_current_genome(master.companion_id)
        if latest is None:
            genome_id = _generated_id("g", master.companion_id, "onboarding")
            latest = await store.persona_repo.create_genome(
                genome_id=genome_id,
                companion_id=master.companion_id,
                source_json={"source_type": "owner_onboarding_repair", "owner_id": owner_id},
                genome_json=_genome_json(payload),
                change_summary="Onboarding repair genome",
            )
        await store.companions.set_current_genome(master.companion_id, latest.genome_id)
    if not await _has_active_memory_realm(store, master):
        await store.workspace_provisioning.ensure_memory_realm(
            owner_id=owner_id,
            companion_id=master.companion_id,
        )
    await store.workspace_provisioning.ensure_web_body(
        owner_id=owner_id,
        companion_id=master.companion_id,
    )


async def _select_owner(
    store: DataStore,
    owner_id: str | None,
    *,
    owners: list[Any] | None = None,
) -> Any | None:
    requested = (owner_id or "").strip()
    if requested:
        row = await store.owners.get(requested)
        return row if row is not None and row.status == "active" else None
    active = owners if owners is not None else [o for o in await store.owners.list() if o.status == "active"]
    configured = (os.environ.get("EIDOLON_LOCAL_OWNER_ID") or "").strip()
    if configured:
        found = next((o for o in active if o.owner_id == configured), None)
        if found is not None:
            return found
    return active[0] if active else None


def _find_master(companions: list[Any]) -> Any | None:
    for companion in companions:
        if _companion_type(companion) == COMPANION_TYPE_MASTER:
            return companion
    for companion in companions:
        if bool(getattr(companion, "is_master", False)):
            return companion
    return None


async def _has_current_genome(store: DataStore, companion: Any) -> bool:
    genome_id = getattr(companion, "current_genome_id", None)
    if not genome_id:
        return False
    genome = await store.persona_repo.get_genome(genome_id)
    return genome is not None and genome.companion_id == companion.companion_id


async def _has_active_memory_realm(store: DataStore, companion: Any) -> bool:
    realm_id = getattr(companion, "default_memory_realm_id", None)
    if not realm_id:
        return False
    realm = await store.memory_repo.get_realm(realm_id)
    return realm is not None and realm.status == "active" and realm.companion_id == companion.companion_id


async def _web_device(store: DataStore, companion_id: str) -> Any | None:
    devices = await store.devices.list_devices_for_companion(companion_id)
    return next(
        (
            row
            for row in devices
            if row.kind == "web" and getattr(row, "revoked_at", None) is None
        ),
        None,
    )


def _workspace_ids(
    *,
    owner_id: str,
    companion_name: str,
    companion_id: str | None,
    role: str,
) -> dict[str, str]:
    companion = (
        _normalize_id(companion_id, fallback="companion", max_len=64)
        if companion_id
        else _generated_id("c", owner_id, companion_name or role)
    )
    return {
        "companion_id": companion,
        "genome_id": _generated_id("g", companion, "origin"),
        "realm_id": _generated_id("r", companion),
    }


def _owner_id(owner_id: str | None, display_name: str) -> str:
    if owner_id and owner_id.strip():
        return _normalize_id(owner_id, fallback="owner", max_len=48)
    return _generated_id("owner", display_name or "me", max_len=48)


def _generated_id(prefix: str, *parts: str, max_len: int = 64) -> str:
    slug = "_".join(
        part for part in (_normalize_id(value, fallback="", max_len=max_len) for value in parts) if part
    )
    suffix = uuid4().hex[:8]
    base = f"{prefix}_{slug}" if slug else prefix
    allowed = max_len - len(suffix) - 1
    base = base[:allowed].strip("._-") or prefix
    return f"{base}_{suffix}"


def _normalize_id(value: str, *, fallback: str, max_len: int) -> str:
    slug = SAFE_ID_RE.sub("_", (value or "").strip()).strip("._-")
    if not slug or not re.match(r"^[a-zA-Z0-9]", slug):
        slug = fallback
    return (slug[:max_len].strip("._-") or fallback)[:max_len]


def _companion_profile(payload: Any) -> JsonDict:
    return {
        "summary": (payload.character_portrait or payload.self_concept or "").strip(),
    }


def _genome_json(payload: Any) -> JsonDict:
    genome = build_persona_genome_from_draft(
        _authoring_draft(payload),
        origin="owner_onboarding",
    )
    return persona_genome_to_json(genome)


def _authoring_draft(payload: Any) -> PersonaAuthoringDraft:
    values: dict[str, Any] = {
        "name": (payload.companion_display_name or "").strip() or "Eidolon",
    }
    for field_name in (
        "self_concept",
        "character_portrait",
        "relationship_narrative",
        "voice_portrait",
    ):
        value = getattr(payload, field_name, None)
        if value is not None:
            values[field_name] = str(value).strip()
    for field_name in (
        "values",
        "boundaries",
        "commitments",
        "behavior_guidance",
        "dialogue_examples",
        "pinned_facts",
        "safety_boundaries",
    ):
        value = getattr(payload, field_name, None)
        if value is not None:
            values[field_name] = _payload_lines(payload, field_name)
    return PersonaAuthoringDraft(**values)


def _split_lines(value: str) -> list[str]:
    return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]


def _payload_lines(payload: Any, name: str) -> list[str]:
    value = getattr(payload, name, None)
    if isinstance(value, list):
        return [str(item).strip(" -\t") for item in value if str(item).strip(" -\t")]
    if isinstance(value, str):
        return _split_lines(value)
    return []


def _launch_identity(owner_id: str, companion_id: str, device_id: str) -> LaunchIdentity:
    query = urlencode(
        {
            "owner_id": owner_id,
            "companion_id": companion_id,
            "device_id": device_id,
        }
    )
    return LaunchIdentity(
        owner_id=owner_id,
        companion_id=companion_id,
        device_id=device_id,
        launch_url=f"{_client_web_base()}/?{query}",
    )


def _client_web_base() -> str:
    return (
        os.environ.get("EIDOLON_CLIENT_WEB_URL")
        or os.environ.get("VITE_CLIENT_WEB_URL")
        or "http://127.0.0.1:3000"
    ).rstrip("/")


async def _reconcile_memory_supervisor(request: Request) -> None:
    client = getattr(request.app.state, "memory_supervisor_client", None)
    if client is None:
        return
    try:
        await client.reconcile()
    except Exception:  # noqa: BLE001 - convenience nudge only
        logger.exception("memory supervisor reconcile failed after onboarding")


def _schedule_memory_supervisor_reconcile(request: Request) -> None:
    client = getattr(request.app.state, "memory_supervisor_client", None)
    if client is None:
        return
    tasks: set[asyncio.Task] = getattr(request.app.state, "onboarding_background_tasks", set())
    request.app.state.onboarding_background_tasks = tasks

    async def _run() -> None:
        await _reconcile_memory_supervisor(request)

    task = asyncio.create_task(_run(), name="onboarding-memory-supervisor-reconcile")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _store(request: Request) -> DataStore:
    store = getattr(request.app.state, "data_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "eidolon_data unavailable")
    return store


def _raise_workspace_error(exc: OwnerWorkspaceError) -> None:
    message = str(exc)
    if "not found" in message:
        raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
    if "already" in message:
        raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
    raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc


def _owner(row: Any) -> OwnerView:
    return OwnerView(
        owner_id=row.owner_id,
        display_name=row.display_name,
        kind=row.kind,
        status=row.status,
        profile_json=row.profile_json or {},
        settings_json=row.settings_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _companion(row: Any) -> CompanionView:
    return CompanionView(
        companion_id=row.companion_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        kind=row.kind,
        status=row.status,
        is_master=bool(getattr(row, "is_master", False)),
        companion_type=_companion_type(row),
        current_genome_id=row.current_genome_id,
        default_memory_realm_id=row.default_memory_realm_id,
        profile_json=row.profile_json or {},
        runtime_config_json=row.runtime_config_json or {},
        metadata_json=row.metadata_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _companion_type(row: Any) -> str:
    value = str(getattr(row, "companion_type", "") or "").strip()
    if value in {COMPANION_TYPE_MASTER, COMPANION_TYPE_SLAVE}:
        return value
    return COMPANION_TYPE_MASTER if bool(getattr(row, "is_master", False)) else COMPANION_TYPE_SLAVE


def _persona_genome(row: Any) -> PersonaGenomeView:
    return PersonaGenomeView(
        genome_id=row.genome_id,
        companion_id=row.companion_id,
        version=row.version,
        status=row.status,
        base_genome_id=row.base_genome_id,
        schema_version=row.schema_version,
        genome_hash=row.genome_hash,
        realizer_version=row.realizer_version,
        applied_event_id=row.applied_event_id,
        source_json=row.source_json or {},
        genome_json=row.genome_json or {},
        change_summary=row.change_summary or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _memory_realm(row: Any) -> MemoryRealmView:
    return MemoryRealmView(
        realm_id=row.realm_id,
        owner_id=row.owner_id,
        companion_id=row.companion_id,
        engine=row.engine,
        engine_config_json=row.engine_config_json or {},
        policy_json=row.policy_json or {},
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _device(row: Any) -> DeviceView:
    return DeviceView(
        device_id=row.device_id,
        owner_id=row.owner_id,
        name=row.name,
        kind=row.kind,
        status=row.status,
        approved_at=row.approved_at,
        approved_by=row.approved_by,
        bound_companion_id=row.bound_companion_id,
        interaction_mode=row.interaction_mode,
        auth_type=row.auth_type,
        capabilities_json=row.capabilities_json or {},
        network_json=row.network_json or {},
        access_policy_json=row.access_policy_json or {},
        metadata_json=row.metadata_json or {},
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
    )
