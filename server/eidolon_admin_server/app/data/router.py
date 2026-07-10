"""Owner-scoped views over Eidolon Data."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from eidolon_data import DataStore
from eidolon_data.services import CompanionDeletionError, OwnerWorkspaceError
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

from .hub_client import HubRuntimeUnavailable
from .owner_backup import create_owner_backup
from .owner_delete_finalizer import (
    OwnerDeleteJournal,
    finalize_owner_delete_jobs,
    owner_cleanup_counts,
    purge_memory_realms,
)
from .schemas import (
    BootstrapResponse,
    CompanionListResponse,
    CompanionView,
    ConversationListResponse,
    ConversationView,
    DeviceAddToOwnerRequest,
    DeviceListResponse,
    DeviceUpdateRequest,
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
    OwnerDeleteResponse,
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
logger = logging.getLogger(__name__)


@router.get("/owners", response_model=OwnerListResponse)
@router.get("/data/owners", response_model=OwnerListResponse, include_in_schema=False)
async def list_owners(request: Request) -> OwnerListResponse:
    rows = await _store(request).owners.list()
    return OwnerListResponse(owners=[_owner(row) for row in rows])


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_local_body(
    request: Request,
    owner_id: str | None = Query(
        default=None,
        description="Resolve this specific owner (used by the client's fallback picker). "
        "When omitted, the host-local default owner is auto-resolved.",
    ),
) -> BootstrapResponse:
    """Resolve an owner → master companion → web body so the web client can
    auto-connect with zero forms.

    Owner resolution: the explicit ``owner_id`` if given, else the sole active
    owner, else the one named by ``EIDOLON_LOCAL_OWNER_ID``; otherwise 409
    (ambiguous). The master is used if present, provisioned if the owner has no
    companions, or a lone non-master companion is promoted; multiple companions
    with no master is 409.
    """
    store = _store(request)
    owners = [o for o in await store.owners.list() if o.status == "active"]
    requested = (owner_id or "").strip()
    configured = (os.environ.get("EIDOLON_LOCAL_OWNER_ID") or "").strip()
    if requested:
        owner = next((o for o in owners if o.owner_id == requested), None)
        if owner is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"owner {requested!r} not found among active owners",
            )
    elif configured:
        owner = next((o for o in owners if o.owner_id == configured), None)
        if owner is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"EIDOLON_LOCAL_OWNER_ID={configured!r} not found among active owners",
            )
    elif len(owners) == 1:
        owner = owners[0]
    elif not owners:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active owner exists")
    else:
        ids = ", ".join(o.owner_id for o in owners)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"multiple owners ({ids}); set EIDOLON_LOCAL_OWNER_ID to disambiguate",
        )

    companions = await store.companions.list_for_owner(owner.owner_id)
    active = [c for c in companions if c.status == "active"]
    master = next((c for c in active if bool(getattr(c, "is_master", False))), None)
    master_source = "existing"
    try:
        if master is None and not active:
            result = await store.workspace_provisioning.provision_workspace(
                owner_id=owner.owner_id, is_master=True
            )
            master = result.companion
            master_source = "provisioned"
        elif master is None and len(active) == 1:
            master = await store.workspace_provisioning.promote_to_master(
                owner_id=owner.owner_id, companion_id=active[0].companion_id
            )
            master_source = "promoted"
        elif master is None:
            ids = ", ".join(c.companion_id for c in active)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"owner {owner.owner_id!r} has multiple companions ({ids}) but no master",
            )
        device = await store.workspace_provisioning.ensure_web_body(
            owner_id=owner.owner_id, companion_id=master.companion_id
        )
    except OwnerWorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return BootstrapResponse(
        owner_id=owner.owner_id,
        owner_display_name=owner.display_name or owner.owner_id,
        companion_id=master.companion_id,
        companion_display_name=master.display_name or master.companion_id,
        device_id=device.device_id,
        master_source=master_source,
    )


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
    await store.events.record_event(
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
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="owner",
        subject_id=owner_id,
        event_type="owner.archived",
        actor_type="admin",
    )
    return _owner(row)


@router.delete("/owners/{owner_id}", response_model=OwnerDeleteResponse)
async def delete_owner(
    owner_id: str,
    request: Request,
    confirm_owner_id: str = Query(
        default="",
        description="Must exactly match owner_id. Required to prevent accidental deletion.",
    ),
    purge_memory: bool = Query(
        default=True,
        description="Also stop memory workers and trash memory palaces for removed realms.",
    ),
) -> OwnerDeleteResponse:
    """Hard-delete an owner and all owner-scoped data after explicit
    confirmation. This removes the owner row, companions, devices, memories,
    runtime rows, conversations, jobs, events, and persona genomes."""
    if confirm_owner_id != owner_id:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            "confirm_owner_id must exactly match owner_id",
        )
    store = _store(request)
    await _require_owner(store, owner_id)
    progress: list[dict[str, Any]] = [
        _delete_progress("confirmed", "二次确认", "completed", 10),
    ]
    realm_ids = [
        row.realm_id for row in await store.memory_repo.list_realms_for_owner(owner_id)
    ]
    try:
        backup = await create_owner_backup(store, owner_id)
    except Exception as exc:  # noqa: BLE001 - abort before writing delete journal
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"owner backup failed; delete aborted: {exc}",
        ) from exc
    progress.append(
        _delete_progress(
            "backup",
            "备份 owner / companion / memory",
            "completed",
            35,
            {"backup_id": backup.get("backup_id"), "path": backup.get("path")},
        )
    )
    journal = OwnerDeleteJournal()
    job = journal.create_or_load(owner_id=owner_id, realm_ids=realm_ids, backup=backup)
    progress.append(
        _delete_progress(
            "journal",
            "写入可恢复删除任务",
            "completed",
            45,
            {"job_id": job["job_id"]},
        )
    )
    result = await store.dev_maintenance.delete_owner_tree(owner_id)
    if not result.deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found")
    job = journal.mark_db_deleted(job, result)
    progress.append(
        _delete_progress("database", "删除 owner 数据库关系树", "completed", 70)
    )

    memory: dict[str, Any] = {
        "purged": False,
        "journaled": True,
        "job_id": job["job_id"],
    }
    if purge_memory:
        memory = await finalize_owner_delete_jobs(
            store,
            getattr(request.app.state, "memory_supervisor_client", None),
            journal=journal,
            only_owner_id=owner_id,
        )
    else:
        memory["pending"] = 1
        memory["skipped_immediate_purge"] = True
    memory_complete = bool(purge_memory and memory.get("pending", 0) == 0)
    progress.append(
        _delete_progress(
            "memory",
            "清理 memory runtime 和 palace",
            "completed" if memory_complete else "pending",
            95 if memory_complete else 82,
            memory,
        )
    )
    progress.append(
        _delete_progress(
            "done",
            "删除流程完成",
            "completed" if memory_complete else "pending",
            100 if memory_complete else 90,
        )
    )
    return OwnerDeleteResponse(
        owner_id=owner_id,
        deleted=True,
        counts=owner_cleanup_counts(result),
        realm_ids=job.get("realm_ids", result.realm_ids),
        backup=backup,
        progress=progress,
        memory=memory,
    )


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
        initialized=_master_ready(companions, persona_genomes, memory_realms, devices),
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
    # The owner's first companion is their master companion; it is provisioned
    # with a host-local web body so a fresh owner is conversation-ready.
    existing_companions = await store.companions.list_for_owner(owner_id)
    is_master = not existing_companions
    try:
        result = await store.workspace_provisioning.provision_workspace(
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
            realm_id=payload.realm_id,
            memory_engine=payload.memory_engine,
            memory_engine_config_json=payload.memory_engine_config_json,
            memory_policy_json=payload.memory_policy_json,
            actor_type="admin",
            is_master=is_master,
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
    _schedule_memory_supervisor_reconcile(request, reason="workspace-initialize")
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


@router.get(
    "/owners/{owner_id}/companions/{companion_id}/devices",
    response_model=DeviceListResponse,
)
async def list_companion_devices(
    owner_id: str,
    companion_id: str,
    request: Request,
) -> DeviceListResponse:
    """All bodies (web + physical) bound to a companion."""
    store = _store(request)
    await _require_owner(store, owner_id)
    await _require_owner_companion(store, owner_id, companion_id)
    rows = await store.devices.list_devices_for_companion(companion_id)
    return DeviceListResponse(devices=[_device(row) for row in rows])


@router.post(
    "/owners/{owner_id}/companions/{companion_id}/devices/web",
    response_model=DeviceView,
)
async def ensure_companion_web_body(
    owner_id: str,
    companion_id: str,
    request: Request,
) -> DeviceView:
    """Idempotently attach a host-local web body to a companion (one click)."""
    store = _store(request)
    await _require_owner(store, owner_id)
    await _require_owner_companion(store, owner_id, companion_id)
    try:
        row = await store.workspace_provisioning.ensure_web_body(
            owner_id=owner_id, companion_id=companion_id
        )
    except OwnerWorkspaceError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc
    return _device(row)


@router.post(
    "/owners/{owner_id}/companions/{companion_id}/promote-master",
    response_model=CompanionView,
)
async def promote_companion_master(
    owner_id: str,
    companion_id: str,
    request: Request,
) -> CompanionView:
    """Make a companion the owner's master and ensure it is conversation-ready
    (current genome + memory realm + host-local web body). Demotes any prior
    master. Idempotent."""
    store = _store(request)
    await _require_owner(store, owner_id)
    try:
        row = await store.workspace_provisioning.promote_to_master(
            owner_id=owner_id, companion_id=companion_id
        )
    except OwnerWorkspaceError as exc:
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in message else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, message) from exc
    # Best-effort: nudge the memory supervisor so the (possibly new) realm's
    # worker comes up without waiting for the next periodic reconcile.  Do not
    # make the operator wait for memory worker warm-up inside this request.
    _schedule_memory_supervisor_reconcile(request, reason="promote-master")
    return _companion(row)


@router.delete("/owners/{owner_id}/companions/{companion_id}")
async def delete_companion(
    owner_id: str,
    companion_id: str,
    request: Request,
    purge_memory: bool = Query(
        default=True,
        description="Also stop the worker and trash the memory palace for each removed realm.",
    ),
) -> dict[str, Any]:
    """Hard-delete a non-master companion and everything referencing it, then
    (optionally) purge its memory palaces. Refuses to delete a master companion
    — promote a replacement first."""
    store = _store(request)
    await _require_owner(store, owner_id)
    companion = await store.companions.get(companion_id)
    if companion is None or companion.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "companion not found")
    if bool(getattr(companion, "is_master", False)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "refusing to delete master companion; promote a replacement first",
        )
    try:
        result = await store.companion_deletion.delete_companion(
            owner_id=owner_id, companion_id=companion_id
        )
    except CompanionDeletionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    memory: dict[str, Any] = {"purged": False}
    if purge_memory and result.realm_ids:
        memory = await purge_memory_realms(
            getattr(request.app.state, "memory_supervisor_client", None),
            result.realm_ids,
        )
    return {
        "owner_id": owner_id,
        "companion_id": companion_id,
        "deleted": True,
        "counts": result.counts,
        "realm_ids": result.realm_ids,
        "device_ids": result.device_ids,
        "memory": memory,
    }


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


@router.post(
    "/owners/{owner_id}/companions/{companion_id}/genome/reset-to-origin",
    response_model=PersonaGenomeView,
)
async def reset_companion_genome_to_origin(
    owner_id: str,
    companion_id: str,
    request: Request,
) -> PersonaGenomeView:
    """Reset a companion to its authored origin genome (drops evolution drift)."""
    store = _store(request)
    await _require_owner(store, owner_id)
    try:
        row = await store.persona.reset_to_origin(owner_id=owner_id, companion_id=companion_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _persona_genome(row)


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
    hub = _hub_device_client(request)
    if hub is None:
        return NearbyDeviceListResponse(devices=[], hub_available=False)
    try:
        runtime_devices = await hub.list_devices()
    except HubRuntimeUnavailable as exc:
        status_code = exc.status_code
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
    hub = _require_hub_device_client(request)
    try:
        runtime_device = await hub.get_device(device_id)
        if not runtime_device.approved:
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                "device must be approved in Hub before Owner actions",
            )
        return await hub.identify_device(device_id)
    except HubRuntimeUnavailable as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


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
        await _require_owner_companion(store, owner_id, payload.companion_id)

    hub = _require_hub_device_client(request)
    try:
        runtime_device = await hub.get_device(device_id)
        if not runtime_device.approved:
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                "device must be approved in Hub before claiming",
            )
    except HubRuntimeUnavailable as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

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
    try:
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
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.claimed",
        actor_type="admin",
        payload_json={"previous_owner_id": None},
    )
    await store.events.record_event(
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
    await hub.refresh_device_config(device_id)
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/approve", response_model=DeviceView)
async def approve_owner_device(owner_id: str, device_id: str, request: Request) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    row = await store.devices.approve(device.device_id, actor_id="admin")
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.approved",
        actor_type="admin",
    )
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/identify")
async def identify_owner_device(owner_id: str, device_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    hub = _require_hub_device_client(request)
    try:
        return await hub.identify_device(device.device_id)
    except HubRuntimeUnavailable as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/owners/{owner_id}/devices/{device_id}/revoke", response_model=DeviceView)
async def revoke_owner_device(owner_id: str, device_id: str, request: Request) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    row = await store.devices.revoke(device.device_id)
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.revoked",
        actor_type="admin",
    )
    return _device(row)


@router.post("/owners/{owner_id}/devices/{device_id}/release", response_model=DeviceView)
async def release_owner_device(owner_id: str, device_id: str, request: Request) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    row = await store.devices.release(device.device_id)
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.released",
        actor_type="admin",
    )
    return _device(row)


@router.patch("/owners/{owner_id}/devices/{device_id}", response_model=DeviceView)
async def update_owner_device(
    owner_id: str,
    device_id: str,
    payload: DeviceUpdateRequest,
    request: Request,
) -> DeviceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    device = await _require_owner_device(store, owner_id, device_id)
    metadata_json = (
        {**(device.metadata_json or {}), **payload.metadata_json}
        if payload.metadata_json is not None
        else None
    )
    row = await store.devices.update_device(
        device.device_id,
        name=payload.name.strip() if isinstance(payload.name, str) and payload.name.strip() else None,
        metadata_json=metadata_json,
    )
    await store.events.record_event(
        event_id=_event_id(),
        owner_id=owner_id,
        subject_type="device",
        subject_id=device_id,
        event_type="device.updated",
        actor_type="admin",
        payload_json={"name": payload.name, "metadata_json": payload.metadata_json or {}},
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
        await _require_owner_companion(store, owner_id, companion_id)
    try:
        row = await store.devices.bind_companion(device.device_id, companion_id=companion_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await store.events.record_event(
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
    await store.events.record_event(
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
    await store.events.record_event(
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


def _hub_device_client(request: Request) -> Any | None:
    return getattr(request.app.state, "hub_device_client", None)


def _require_hub_device_client(request: Request) -> Any:
    client = _hub_device_client(request)
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Hub device runtime unavailable")
    return client


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


async def _require_owner_companion(store: DataStore, owner_id: str, companion_id: str) -> Any:
    companion = await store.companions.get(companion_id)
    if companion is None or companion.owner_id != owner_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "companion does not belong to owner")
    if companion.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "companion is not active")
    return companion


async def _require_owner_job(store: DataStore, owner_id: str, job_id: str) -> Any:
    rows = await store.jobs.list_for_owner(owner_id, limit=500)
    for row in rows:
        if row.job_id == job_id:
            return row
    raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")


def _event_id() -> str:
    from uuid import uuid4

    return f"evt-{uuid4().hex}"


def _delete_progress(
    key: str,
    label: str,
    status_text: str,
    progress: int,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "key": key,
        "label": label,
        "status": status_text,
        "progress": progress,
    }
    if detail:
        item["detail"] = detail
    return item


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
        companion_type=str(
            getattr(
                row,
                "companion_type",
                "master" if bool(getattr(row, "is_master", False)) else "slave",
            )
            or ("master" if bool(getattr(row, "is_master", False)) else "slave")
        ),
        current_genome_id=row.current_genome_id,
        default_memory_realm_id=row.default_memory_realm_id,
        profile_json=row.profile_json or {},
        runtime_config_json=row.runtime_config_json or {},
        metadata_json=row.metadata_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _master_ready(
    companions: list[Any],
    persona_genomes: list[Any],
    memory_realms: list[Any],
    devices: list[Any],
) -> bool:
    active = [row for row in companions if getattr(row, "status", "") == "active"]
    master = next(
        (
            row
            for row in active
            if getattr(row, "companion_type", "") == "master"
            or bool(getattr(row, "is_master", False))
        ),
        None,
    )
    if master is None:
        return False
    if not getattr(master, "current_genome_id", None):
        return False
    has_current_genome = any(
        row.genome_id == master.current_genome_id and row.companion_id == master.companion_id
        for row in persona_genomes
    )
    has_memory_realm = any(
        row.realm_id == master.default_memory_realm_id
        and row.companion_id == master.companion_id
        and row.status == "active"
        for row in memory_realms
    )
    has_web_device = any(
        row.bound_companion_id == master.companion_id
        and row.kind == "web"
        and row.revoked_at is None
        for row in devices
    )
    return has_current_genome and has_memory_realm and has_web_device


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
        runtime_caller_id=row.runtime_caller_id,
        runtime_session_id=row.runtime_session_id,
        device_id=row.source_device_id,
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
        companion_id=getattr(row, "companion_id", None),
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        event_type=row.event_type,
        event_class=getattr(row, "event_class", "audit"),
        source=getattr(row, "source", "data"),
        severity=getattr(row, "severity", "info"),
        outcome=getattr(row, "outcome", "success"),
        reason=getattr(row, "reason", None),
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        trace_id=getattr(row, "trace_id", None),
        data_classification=getattr(row, "data_classification", "safe"),
        payload_json=row.payload_json or {},
        occurred_at=getattr(row, "occurred_at", None),
        created_at=row.created_at,
    )


def _schedule_memory_supervisor_reconcile(request: Request, *, reason: str) -> None:
    client = getattr(request.app.state, "memory_supervisor_client", None)
    if client is None:
        return
    tasks: set[asyncio.Task] = getattr(request.app.state, "data_background_tasks", set())
    request.app.state.data_background_tasks = tasks

    async def _run() -> None:
        try:
            await client.reconcile()
        except Exception:  # noqa: BLE001 - convenience nudge only
            logger.exception("memory supervisor reconcile failed after %s", reason)

    task = asyncio.create_task(_run(), name=f"data-memory-supervisor-reconcile-{reason}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)
