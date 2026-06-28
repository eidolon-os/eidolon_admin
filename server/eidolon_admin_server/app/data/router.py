"""Owner-scoped views over Eidolon Data."""

from __future__ import annotations

from typing import Any

from eidolon_data import DataStore
from eidolon_data.services import OwnerWorkspaceError
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

from .schemas import (
    CompanionListResponse,
    CompanionView,
    ConversationListResponse,
    ConversationView,
    DeviceAddToOwnerRequest,
    DeviceListResponse,
    DeviceView,
    EventListResponse,
    EventView,
    JobListResponse,
    JobView,
    MemoryRealmListResponse,
    MemoryRealmView,
    NearbyDeviceListResponse,
    NearbyDeviceView,
    OwnerCounts,
    OwnerCreateRequest,
    OwnerListResponse,
    OwnerOverviewResponse,
    OwnerUpdateRequest,
    OwnerView,
    PersonaGenomeListResponse,
    PersonaGenomeView,
    WorkspaceInitializeRequest,
    WorkspaceInitializeResponse,
)

router = APIRouter(tags=["owners"])


@router.get("/owners", response_model=OwnerListResponse)
@router.get("/data/owners", response_model=OwnerListResponse, include_in_schema=False)
async def list_owners(request: Request) -> OwnerListResponse:
    rows = await _store(request).owners.list()
    return OwnerListResponse(owners=[_owner(row) for row in rows])


@router.post("/owners", response_model=OwnerView, status_code=status.HTTP_201_CREATED)
@router.post(
    "/data/owners",
    response_model=OwnerView,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_owner(payload: OwnerCreateRequest, request: Request) -> OwnerView:
    store = _store(request)
    try:
        result = await store.owner_service.create_owner(
            owner_id=payload.owner_id,
            display_name=payload.display_name,
            kind=payload.kind,
            profile_json=payload.profile_json,
            settings_json=payload.settings_json,
            actor_type="admin",
        )
    except OwnerWorkspaceError as exc:
        status_code = status.HTTP_409_CONFLICT if "already exists" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "owner already exists") from exc
    return _owner(result.owner)


@router.get("/owners/{owner_id}", response_model=OwnerView)
@router.get("/data/owners/{owner_id}", response_model=OwnerView, include_in_schema=False)
async def get_owner(owner_id: str, request: Request) -> OwnerView:
    return _owner(await _require_owner(_store(request), owner_id))


@router.patch("/owners/{owner_id}", response_model=OwnerView)
async def update_owner(owner_id: str, payload: OwnerUpdateRequest, request: Request) -> OwnerView:
    store = _store(request)
    await _require_owner(store, owner_id)
    try:
        row = await store.owners.update(
            owner_id,
            display_name=payload.display_name,
            kind=payload.kind,
            profile_json=payload.profile_json,
            settings_json=payload.settings_json,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found") from exc
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="owner",
        subject_id=owner_id,
        event_type="owner.updated",
        actor_type="admin",
        payload_json=payload.model_dump(exclude_none=True),
    )
    return _owner(row)


@router.post("/owners/{owner_id}/archive", response_model=OwnerView)
async def archive_owner(owner_id: str, request: Request) -> OwnerView:
    store = _store(request)
    await _require_owner(store, owner_id)
    try:
        row = await store.owners.archive(owner_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found") from exc
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="owner",
        subject_id=owner_id,
        event_type="owner.archived",
        actor_type="admin",
    )
    return _owner(row)


@router.get("/owners/{owner_id}/workspace", response_model=OwnerOverviewResponse)
@router.get("/owners/{owner_id}/overview", response_model=OwnerOverviewResponse, include_in_schema=False)
@router.get("/data/owners/{owner_id}/overview", response_model=OwnerOverviewResponse, include_in_schema=False)
async def get_owner_overview(owner_id: str, request: Request) -> OwnerOverviewResponse:
    store = _store(request)
    owner = await _require_owner(store, owner_id)
    companions = await store.companions.list_for_owner(owner_id)
    persona_genomes = await store.persona_repo.list_for_companions(
        [row.companion_id for row in companions]
    )
    devices = await store.devices.list_devices_for_owner(owner_id)
    conversations = await store.conversations.list_for_owner(owner_id, limit=20)
    memory_realms = await store.memory_repo.list_realms_for_owner(owner_id)
    jobs = await store.jobs.list_for_owner(owner_id, limit=20)
    events = await store.events.list_for_owner(owner_id, limit=20)
    return OwnerOverviewResponse(
        owner=_owner(owner),
        counts=OwnerCounts(
            companions=len(companions),
            persona_genomes=len(persona_genomes),
            devices=len(devices),
            conversations=len(conversations),
            memory_realms=len(memory_realms),
            jobs=len(jobs),
            events=len(events),
        ),
        initialized=bool(companions and persona_genomes and memory_realms),
        companions=[_companion(row) for row in companions[:10]],
        devices=[_device(row) for row in devices[:10]],
        conversations=[_conversation(row) for row in conversations],
        memory_realms=[_memory_realm(row) for row in memory_realms[:10]],
        jobs=[_job(row) for row in jobs],
        events=[_event(row) for row in events],
    )


@router.post("/owners/{owner_id}/workspace/initialize", response_model=WorkspaceInitializeResponse)
async def initialize_owner_workspace(
    owner_id: str,
    payload: WorkspaceInitializeRequest,
    request: Request,
) -> WorkspaceInitializeResponse:
    store = _store(request)
    try:
        result = await store.companion_workspace.initialize_workspace(
            owner_id=owner_id,
            companion_id=payload.companion_id,
            companion_display_name=payload.companion_display_name,
            companion_kind=payload.companion_kind,
            companion_profile_json=payload.companion_profile_json,
            companion_runtime_config_json=payload.companion_runtime_config_json,
            companion_metadata_json=payload.companion_metadata_json,
            genome_id=payload.genome_id,
            genome_source_json=payload.genome_source_json,
            genome_json=payload.genome_json,
            prompt_markdown=payload.prompt_markdown,
            evolution_state_json=payload.evolution_state_json,
            realm_id=payload.realm_id,
            memory_engine=payload.memory_engine,
            memory_engine_config_json=payload.memory_engine_config_json,
            memory_policy_json=payload.memory_policy_json,
            actor_type="admin",
        )
    except OwnerWorkspaceError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
        if "already" in message:
            raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "workspace already initialized") from exc
    return WorkspaceInitializeResponse(
        companion=_companion(result.companion),
        persona_genome=_persona_genome(result.persona_genome),
        memory_realm=_memory_realm(result.memory_realm),
    )


@router.get("/owners/{owner_id}/companions", response_model=CompanionListResponse)
@router.get("/data/owners/{owner_id}/companions", response_model=CompanionListResponse, include_in_schema=False)
async def list_owner_companions(owner_id: str, request: Request) -> CompanionListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.companions.list_for_owner(owner_id)
    return CompanionListResponse(companions=[_companion(row) for row in rows])


@router.get("/owners/{owner_id}/persona-genomes", response_model=PersonaGenomeListResponse)
@router.get(
    "/data/owners/{owner_id}/persona-genomes",
    response_model=PersonaGenomeListResponse,
    include_in_schema=False,
)
async def list_owner_persona_genomes(
    owner_id: str,
    request: Request,
) -> PersonaGenomeListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    companions = await store.companions.list_for_owner(owner_id)
    rows = await store.persona_repo.list_for_companions([row.companion_id for row in companions])
    return PersonaGenomeListResponse(persona_genomes=[_persona_genome(row) for row in rows])


@router.get("/owners/{owner_id}/devices", response_model=DeviceListResponse)
@router.get("/data/owners/{owner_id}/devices", response_model=DeviceListResponse, include_in_schema=False)
async def list_owner_devices(owner_id: str, request: Request) -> DeviceListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.devices.list_devices_for_owner(owner_id)
    return DeviceListResponse(devices=[_device(row) for row in rows])


@router.get("/owners/{owner_id}/nearby-devices", response_model=NearbyDeviceListResponse)
async def list_nearby_owner_devices(owner_id: str, request: Request) -> NearbyDeviceListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    orch = _device_orchestrator(request)
    if orch is None:
        return NearbyDeviceListResponse(devices=[], hub_available=False)
    try:
        runtime_devices = await orch.list_devices()
    except Exception as exc:  # noqa: BLE001
        status_code = int(getattr(exc, "status_code", status.HTTP_502_BAD_GATEWAY))
        if status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            return NearbyDeviceListResponse(devices=[], hub_available=False)
        raise HTTPException(status_code, str(exc)) from exc

    nearby: list[NearbyDeviceView] = []
    for device in runtime_devices:
        if not device.approved:
            continue
        if (device.status or "").lower() == "offline":
            continue
        stored = await store.devices.get_device(device.device_id)
        if stored is not None and stored.owner_id is not None:
            continue
        nearby.append(_nearby_device(device))
    return NearbyDeviceListResponse(devices=nearby, hub_available=True)


@router.post("/owners/{owner_id}/nearby-devices/{device_id}/identify")
async def identify_nearby_owner_device(owner_id: str, device_id: str, request: Request) -> dict[str, Any]:
    await _require_owner(_store(request), owner_id)
    orch = _require_device_orchestrator(request)
    try:
        runtime_device = await orch.get_device(device_id)
        if not runtime_device.approved:
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                "device must be approved in Hub before Owner actions",
            )
        return await orch.identify_device(device_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            int(getattr(exc, "status_code", status.HTTP_502_BAD_GATEWAY)),
            str(exc),
        ) from exc


@router.post("/owners/{owner_id}/nearby-devices/{device_id}/claim", response_model=DeviceView)
@router.post(
    "/owners/{owner_id}/nearby-devices/{device_id}/add-to-owner",
    response_model=DeviceView,
    include_in_schema=False,
)
async def claim_nearby_device(
    owner_id: str,
    device_id: str,
    payload: DeviceAddToOwnerRequest,
    request: Request,
) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    existing = await store.devices.get_device(device_id)
    if existing is not None:
        if existing.owner_id == owner_id:
            return _device(existing)
        if existing.owner_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "device already belongs to another owner")

    if payload.companion_id:
        companion = await store.companions.get(payload.companion_id)
        if companion is None or companion.owner_id != owner_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "companion does not belong to owner")

    orch = _require_device_orchestrator(request)
    try:
        runtime_device = await orch.get_device(device_id)
        if not runtime_device.approved:
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                "device must be approved in Hub before claiming",
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            int(getattr(exc, "status_code", status.HTTP_502_BAD_GATEWAY)),
            str(exc),
        ) from exc

    metadata_json = {
        **payload.metadata_json,
        "source": "hub_runtime",
        "hub_approved": runtime_device.approved,
        "hub_enabled": runtime_device.enabled,
    }
    network_json = {
        "runtime_status": runtime_device.status,
        "room_name": runtime_device.room_name,
        "missed_probes": runtime_device.missed_probes,
    }
    if existing is None:
        row = await store.devices.create_device(
            device_id=runtime_device.device_id,
            owner_id=None,
            name=runtime_device.name or runtime_device.device_id,
            kind=runtime_device.kind or "unknown",
            status="discovered",
            network_json=network_json,
            metadata_json=metadata_json,
            last_seen_at=runtime_device.last_seen,
        )
    row = await store.devices.claim_device(
        runtime_device.device_id,
        owner_id=owner_id,
        name=payload.name or runtime_device.name or runtime_device.device_id,
        kind=runtime_device.kind or "unknown",
        companion_id=payload.companion_id,
        interaction_mode=payload.interaction_mode,
        network_json=network_json,
        access_policy_json=payload.access_policy_json,
        metadata_json=metadata_json,
    )
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.claimed",
        actor_type="admin",
        payload_json={"previous_owner_id": None},
    )
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.bound_companion",
        actor_type="admin",
        payload_json={
            "companion_id": payload.companion_id,
            "interaction_mode": payload.interaction_mode,
        },
    )
    await orch.refresh_device_config(device_id)
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/approve", response_model=DeviceView)
async def approve_owner_device(owner_id: str, device_id: str, request: Request) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    row = await store.devices.approve(device.device_id, actor_id="admin")
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.approved",
        actor_type="admin",
    )
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/revoke", response_model=DeviceView)
async def revoke_owner_device(owner_id: str, device_id: str, request: Request) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    row = await store.devices.revoke(device.device_id)
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.revoked",
        actor_type="admin",
    )
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/bind-companion", response_model=DeviceView)
async def bind_owner_device(
    owner_id: str,
    device_id: str,
    request: Request,
    companion_id: str | None = Query(default=None),
) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    if companion_id is not None:
        companion = await store.companions.get(companion_id)
        if companion is None or companion.owner_id != owner_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "companion does not belong to owner")
    row = await store.devices.bind_companion(device.device_id, companion_id=companion_id)
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.bound_companion",
        actor_type="admin",
        payload_json={"companion_id": companion_id},
    )
    return _device(row)


@router.get("/owners/{owner_id}/conversations", response_model=ConversationListResponse)
@router.get(
    "/data/owners/{owner_id}/conversations",
    response_model=ConversationListResponse,
    include_in_schema=False,
)
async def list_owner_conversations(
    owner_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> ConversationListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.conversations.list_for_owner(owner_id, limit=limit)
    return ConversationListResponse(conversations=[_conversation(row) for row in rows])


@router.get("/owners/{owner_id}/memory-realms", response_model=MemoryRealmListResponse)
@router.get(
    "/data/owners/{owner_id}/memory-realms",
    response_model=MemoryRealmListResponse,
    include_in_schema=False,
)
async def list_owner_memory_realms(owner_id: str, request: Request) -> MemoryRealmListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.memory_repo.list_realms_for_owner(owner_id)
    return MemoryRealmListResponse(memory_realms=[_memory_realm(row) for row in rows])


@router.get("/owners/{owner_id}/jobs", response_model=JobListResponse)
@router.get("/data/owners/{owner_id}/jobs", response_model=JobListResponse, include_in_schema=False)
async def list_owner_jobs(
    owner_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> JobListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.jobs.list_for_owner(owner_id, limit=limit)
    return JobListResponse(jobs=[_job(row) for row in rows])


@router.post("/owners/{owner_id}/jobs/{job_id}/cancel", response_model=JobView)
async def cancel_owner_job(owner_id: str, job_id: str, request: Request) -> JobView:
    store = _store(request)
    await _require_owner(store, owner_id)
    job = await _require_owner_job(store, owner_id, job_id)
    row = await store.jobs.update_status(
        job.job_id,
        status="cancelled",
        progress_json={**(job.progress_json or {}), "cancel_requested_by": "admin"},
    )
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="job",
        subject_id=job_id,
        event_type="job.cancel_requested",
        actor_type="admin",
    )
    return _job(row)


@router.post("/owners/{owner_id}/jobs/{job_id}/retry", response_model=JobView)
async def retry_owner_job(owner_id: str, job_id: str, request: Request) -> JobView:
    store = _store(request)
    await _require_owner(store, owner_id)
    job = await _require_owner_job(store, owner_id, job_id)
    row = await store.jobs.update_status(
        job.job_id,
        status="pending",
        progress_json={**(job.progress_json or {}), "retry_requested_by": "admin"},
        error_json={},
    )
    await store.events.append(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="job",
        subject_id=job_id,
        event_type="job.retry_requested",
        actor_type="admin",
    )
    return _job(row)


@router.get("/owners/{owner_id}/events", response_model=EventListResponse)
@router.get("/data/owners/{owner_id}/events", response_model=EventListResponse, include_in_schema=False)
async def list_owner_events(
    owner_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> EventListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    rows = await store.events.list_for_owner(owner_id, limit=limit)
    return EventListResponse(events=[_event(row) for row in rows])


def _store(request: Request) -> DataStore:
    store = getattr(request.app.state, "data_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "eidolon_data unavailable")
    return store


def _device_orchestrator(request: Request) -> Any | None:
    return getattr(request.app.state, "device_orchestrator", None)


def _require_device_orchestrator(request: Request) -> Any:
    orch = _device_orchestrator(request)
    if orch is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "device runtime unavailable")
    return orch


async def _require_owner(store: DataStore, owner_id: str) -> Any:
    row = await store.owners.get(owner_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found")
    return row


async def _require_owner_device(store: DataStore, owner_id: str, device_id: str) -> Any:
    row = await store.devices.get_device(device_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return row


async def _require_owner_job(store: DataStore, owner_id: str, job_id: str) -> Any:
    rows = await store.jobs.list_for_owner(owner_id, limit=500)
    for row in rows:
        if row.job_id == job_id:
            return row
    raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")


def _event_id() -> str:
    from uuid import uuid4

    return f"evt-{uuid4().hex}"


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
        current_genome_id=row.current_genome_id,
        default_memory_realm_id=row.default_memory_realm_id,
        profile_json=row.profile_json or {},
        runtime_config_json=row.runtime_config_json or {},
        metadata_json=row.metadata_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _persona_genome(row: Any) -> PersonaGenomeView:
    return PersonaGenomeView(
        genome_id=row.genome_id,
        companion_id=row.companion_id,
        version=row.version,
        status=row.status,
        base_genome_id=row.base_genome_id,
        source_json=row.source_json or {},
        genome_json=row.genome_json or {},
        prompt_markdown=row.prompt_markdown or "",
        evolution_state_json=row.evolution_state_json or {},
        change_summary=row.change_summary or "",
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


def _nearby_device(row: Any) -> NearbyDeviceView:
    return NearbyDeviceView(
        device_id=row.device_id,
        name=row.name or "",
        kind=row.kind or "unknown",
        enabled=bool(row.enabled),
        approved=bool(row.approved),
        status=row.status or "unknown",
        room_name=row.room_name or "",
        missed_probes=row.missed_probes or 0,
        last_seen=row.last_seen,
    )


def _conversation(row: Any) -> ConversationView:
    return ConversationView(
        conversation_id=row.conversation_id,
        owner_id=row.owner_id,
        companion_id=row.companion_id,
        device_id=row.device_id,
        title=row.title,
        status=row.status,
        started_at=row.started_at,
        updated_at=row.updated_at,
        ended_at=row.ended_at,
        metadata_json=row.metadata_json or {},
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


def _job(row: Any) -> JobView:
    return JobView(
        job_id=row.job_id,
        owner_id=row.owner_id,
        companion_id=row.companion_id,
        conversation_id=row.conversation_id,
        turn_id=row.turn_id,
        provider=row.provider,
        kind=row.kind,
        status=row.status,
        input_json=row.input_json or {},
        provider_ref_json=row.provider_ref_json or {},
        progress_json=row.progress_json or {},
        result_json=row.result_json or {},
        error_json=row.error_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _event(row: Any) -> EventView:
    return EventView(
        event_id=row.event_id,
        owner_id=row.owner_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        event_type=row.event_type,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        payload_json=row.payload_json or {},
        created_at=row.created_at,
    )
