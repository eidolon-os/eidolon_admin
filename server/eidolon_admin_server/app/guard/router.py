"""Explicit Guard ownership and lifecycle API."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

from eidolon_data import DataStore
from eidolon_sdk.biz.guard import normalize_guard_policy_config, normalize_guard_runtime_config
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status

from ..data.schemas import DeviceListResponse, DeviceView
from .schemas import (
    GuardBindingListResponse,
    GuardBindingView,
    GuardClaimRequest,
    GuardConfigUpdateRequest,
    GuardRuntimeConfigUpdateRequest,
    OwnerFaceDeliveryView,
    OwnerFaceProfileDraftRequest,
    OwnerFaceProfileStatusResponse,
    OwnerFaceProfileView,
    OwnerFaceReferenceView,
)
from .owner_face_images import MAX_RAW_IMAGE_BYTES, OwnerFaceImageError, normalize_owner_face_image

router = APIRouter(prefix="/guard", tags=["guard"])
logger = logging.getLogger(__name__)


@router.get("/pending-devices", response_model=DeviceListResponse)
async def list_pending_devices(request: Request) -> DeviceListResponse:
    store = _store(request)
    rows = await store.guard_bindings.list_pending_guard_devices()
    return DeviceListResponse(devices=[_device(row) for row in rows])


@router.get("/owners/{owner_id}/bindings", response_model=GuardBindingListResponse)
async def list_bindings(owner_id: str, request: Request) -> GuardBindingListResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    return GuardBindingListResponse(
        bindings=[_binding(row) for row in await store.guard_bindings.list_for_owner(owner_id)]
    )


@router.post("/owners/{owner_id}/bindings", response_model=GuardBindingView)
async def claim_binding(
    owner_id: str,
    payload: GuardClaimRequest,
    request: Request,
) -> GuardBindingView:
    store = _store(request)
    await _require_owner(store, owner_id)
    _assert_control_only_config(payload.config_json)
    _validate_policy_config(payload.policy_id, payload.config_json)
    device = await store.devices.get_device(payload.device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guard device not found")
    hub_registry = (device.metadata_json or {}).get("hub_registry") or {}
    if not bool(hub_registry.get("approved")):
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            "guard device must be approved in Hub before it can be claimed",
        )
    try:
        binding = await store.guard_bindings.claim(
            owner_id=owner_id,
            device_id=payload.device_id,
            guard_companion_id=payload.companion_id
            or _default_companion_id(owner_id, payload.device_id),
            guard_display_name=payload.display_name,
            policy_id=payload.policy_id,
            config_json=dict(payload.config_json),
            replace=payload.replace,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="guard_binding",
        subject_id=binding.binding_id,
        event_type="guard.binding.claimed",
        actor_type="admin",
        payload_json={"device_id": binding.device_id, "policy_id": binding.policy_id},
    )
    return _binding(binding)


@router.put(
    "/owners/{owner_id}/bindings/{binding_id}/config",
    response_model=GuardBindingView,
)
async def update_binding_config(
    owner_id: str,
    binding_id: str,
    payload: GuardConfigUpdateRequest,
    request: Request,
) -> GuardBindingView:
    """Explicit, revision-checked policy configuration update for one binding."""
    store = _store(request)
    await _require_owner(store, owner_id)
    binding = await store.guard_bindings.get(binding_id)
    if binding is None or binding.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guard binding not found")
    _assert_control_only_config(payload.config_json)
    _validate_policy_config(binding.policy_id, payload.config_json)
    try:
        row = await store.guard_bindings.update_config(
            binding_id=binding_id,
            config_json=dict(payload.config_json),
            expected_revision=payload.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="guard_binding",
        subject_id=row.binding_id,
        event_type="guard.binding.configured",
        actor_type="admin",
        payload_json={"config_revision": row.config_revision, "policy_id": row.policy_id},
    )
    return _binding(row)


@router.put(
    "/owners/{owner_id}/bindings/{binding_id}/runtime-config",
    response_model=GuardBindingView,
)
async def update_binding_runtime_config(
    owner_id: str,
    binding_id: str,
    payload: GuardRuntimeConfigUpdateRequest,
    request: Request,
) -> GuardBindingView:
    """Replace device-local sampling parameters without changing Hub policy."""
    store = _store(request)
    await _require_owner(store, owner_id)
    binding = await store.guard_bindings.get(binding_id)
    if binding is None or binding.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guard binding not found")
    # GuardRuntimeConfig is a strict, extra-forbid whitelist. Unlike the
    # generic policy-config heuristic below, it can safely admit legitimate
    # control fields such as owner_face_interval_ms without mistaking the word
    # "face" for biometric media.
    _validate_runtime_config(payload.runtime_config_json)
    try:
        row = await store.guard_bindings.update_runtime_config(
            binding_id=binding_id,
            runtime_config_json=dict(payload.runtime_config_json),
            expected_revision=payload.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="guard_binding",
        subject_id=row.binding_id,
        event_type="guard.runtime.configured",
        actor_type="admin",
        payload_json={"runtime_revision": row.runtime_revision},
    )
    return _binding(row)


@router.post(
    "/owners/{owner_id}/owner-face-profiles/drafts",
    response_model=OwnerFaceProfileView,
    status_code=status.HTTP_201_CREATED,
)
async def create_owner_face_profile_draft(
    owner_id: str,
    payload: OwnerFaceProfileDraftRequest,
    request: Request,
) -> OwnerFaceProfileView:
    store = _store(request)
    await _require_owner(store, owner_id)
    try:
        row = await store.owner_face_profiles.create_draft(
            owner_id=owner_id,
            model_id=payload.model_id,
            preprocessing_version=payload.preprocessing_version,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    cleanup = await _purge_superseded_owner_face_references(store, owner_id)
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="owner_face_profile",
        subject_id=row.profile_id,
        event_type="guard.owner_face_profile.draft_created",
        actor_type="admin",
        data_classification="sensitive",
        payload_json={
            "profile_revision": row.revision,
            "model_id": row.model_id,
            "preprocessing_version": row.preprocessing_version,
            **cleanup,
        },
    )
    return await _owner_face_profile(store, row)


@router.post(
    "/owners/{owner_id}/owner-face-profiles/{profile_revision_id}/references",
    response_model=OwnerFaceReferenceView,
    status_code=status.HTTP_201_CREATED,
)
async def add_owner_face_reference(
    owner_id: str,
    profile_revision_id: str,
    request: Request,
    pose: str = Query(),
    image: UploadFile = File(),
) -> OwnerFaceReferenceView:
    store = _store(request)
    await _require_owner(store, owner_id)
    profile = await store.owner_face_profiles.get_revision(profile_revision_id)
    if profile is None or profile.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner face profile draft not found")
    raw = await image.read(MAX_RAW_IMAGE_BYTES + 1)
    await image.close()
    try:
        normalized = normalize_owner_face_image(raw)
    except OwnerFaceImageError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        del raw
    digest = hashlib.sha256(normalized).hexdigest()
    storage_key = (
        f"{owner_id}/owner-face/{profile.profile_id}/r{profile.revision}/"
        f"{uuid4().hex}.jpg"
    )
    try:
        store.object_storage.put(storage_key, normalized, expected_sha256=digest)
        reference = await store.owner_face_profiles.add_reference(
            profile_revision_id=profile_revision_id,
            pose=pose,
            content_type="image/jpeg",
            size_bytes=len(normalized),
            sha256=digest,
            storage_key=storage_key,
        )
    except (KeyError, ValueError) as exc:
        store.object_storage.delete(storage_key)
        code = status.HTTP_404_NOT_FOUND if isinstance(exc, KeyError) else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    except Exception:
        store.object_storage.delete(storage_key)
        raise
    finally:
        del normalized
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="owner_face_profile",
        subject_id=profile.profile_id,
        event_type="guard.owner_face_profile.reference_added",
        actor_type="admin",
        data_classification="sensitive",
        payload_json={
            "profile_revision": profile.revision,
            "reference_id": reference.reference_id,
            "pose": reference.pose,
            "sha256": reference.sha256,
        },
    )
    return _owner_face_reference(reference)


@router.post(
    "/owners/{owner_id}/owner-face-profiles/{profile_revision_id}/activate",
    response_model=OwnerFaceProfileView,
)
async def activate_owner_face_profile(
    owner_id: str,
    profile_revision_id: str,
    request: Request,
) -> OwnerFaceProfileView:
    store = _store(request)
    await _require_owner(store, owner_id)
    profile = await store.owner_face_profiles.get_revision(profile_revision_id)
    if profile is None or profile.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner face profile draft not found")
    try:
        row = await store.owner_face_profiles.activate(profile_revision_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    references = await store.owner_face_profiles.list_references(profile_revision_id)
    cleanup = await _purge_superseded_owner_face_references(store, owner_id)
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="owner_face_profile",
        subject_id=row.profile_id,
        event_type="guard.owner_face_profile.desired",
        actor_type="admin",
        data_classification="sensitive",
        payload_json={
            "profile_revision": row.revision,
            "reference_count": len(references),
            **cleanup,
        },
    )
    return await _owner_face_profile(store, row)


@router.post(
    "/owners/{owner_id}/owner-face-profile/clear",
    response_model=OwnerFaceProfileView,
)
async def clear_owner_face_profile(owner_id: str, request: Request) -> OwnerFaceProfileView:
    store = _store(request)
    await _require_owner(store, owner_id)
    current = await store.owner_face_profiles.get_desired_for_owner(owner_id)
    if current is not None and current.desired_state == "cleared":
        await _purge_superseded_owner_face_references(store, owner_id)
        return await _owner_face_profile(store, current)
    try:
        row = await store.owner_face_profiles.clear(owner_id=owner_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    cleanup = await _purge_superseded_owner_face_references(store, owner_id)
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="owner_face_profile",
        subject_id=row.profile_id,
        event_type="guard.owner_face_profile.cleared",
        actor_type="admin",
        data_classification="sensitive",
        payload_json={"profile_revision": row.revision, **cleanup},
    )
    return await _owner_face_profile(store, row)


@router.get(
    "/owners/{owner_id}/owner-face-profile",
    response_model=OwnerFaceProfileStatusResponse,
)
async def get_owner_face_profile_status(
    owner_id: str,
    request: Request,
) -> OwnerFaceProfileStatusResponse:
    store = _store(request)
    await _require_owner(store, owner_id)
    desired = await store.owner_face_profiles.get_desired_for_owner(owner_id)
    if desired is None:
        return OwnerFaceProfileStatusResponse()
    deliveries = []
    for binding in await store.guard_bindings.list_for_owner(owner_id):
        for delivery in await store.guard_owner_face_profile_deliveries.list_for_binding(
            binding.binding_id
        ):
            if (
                delivery.profile_id == desired.profile_id
                and delivery.profile_revision == desired.revision
            ):
                deliveries.append(_owner_face_delivery(delivery))
    return OwnerFaceProfileStatusResponse(
        desired=await _owner_face_profile(store, desired),
        deliveries=deliveries,
    )


@router.post("/owners/{owner_id}/bindings/{binding_id}/disable", response_model=GuardBindingView)
async def disable_binding(owner_id: str, binding_id: str, request: Request) -> GuardBindingView:
    return await _transition_binding(owner_id, binding_id, request, revoke=False)


@router.post("/owners/{owner_id}/bindings/{binding_id}/revoke", response_model=GuardBindingView)
async def revoke_binding(owner_id: str, binding_id: str, request: Request) -> GuardBindingView:
    return await _transition_binding(owner_id, binding_id, request, revoke=True)


async def _transition_binding(
    owner_id: str,
    binding_id: str,
    request: Request,
    *,
    revoke: bool,
) -> GuardBindingView:
    store = _store(request)
    await _require_owner(store, owner_id)
    binding = await store.guard_bindings.get(binding_id)
    if binding is None or binding.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guard binding not found")
    row = await store.guard_bindings.disable(binding_id, revoke=revoke)
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="guard_binding",
        subject_id=row.binding_id,
        event_type="guard.binding.revoked" if revoke else "guard.binding.disabled",
        actor_type="admin",
    )
    await store.events.record_event(
        event_id=f"evt-{uuid4().hex}",
        owner_id=owner_id,
        subject_type="guard_binding",
        subject_id=row.binding_id,
        event_type="guard.runtime.desired_state_changed",
        actor_type="admin",
        payload_json={
            "desired_runtime_state": row.desired_runtime_state,
            "runtime_revision": row.runtime_revision,
        },
    )
    return _binding(row)


def _store(request: Request) -> DataStore:
    store = getattr(request.app.state, "data_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "eidolon_data store unavailable")
    return store


async def _require_owner(store: DataStore, owner_id: str) -> None:
    owner = await store.owners.get(owner_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "owner not found")


async def _purge_superseded_owner_face_references(
    store: DataStore,
    owner_id: str,
) -> dict[str, int]:
    references = await store.owner_face_profiles.list_superseded_references(owner_id)
    removed_reference_ids: list[str] = []
    error_count = 0
    for reference in references:
        try:
            store.object_storage.delete(reference.storage_key)
        except (OSError, ValueError):
            error_count += 1
            logger.exception(
                "Failed to purge superseded Owner Face reference reference_id=%s",
                reference.reference_id,
            )
        else:
            removed_reference_ids.append(reference.reference_id)
    removed_count = await store.owner_face_profiles.delete_superseded_references(
        owner_id=owner_id,
        reference_ids=removed_reference_ids,
    )
    return {
        "purged_reference_count": removed_count,
        "purge_error_count": error_count,
    }


def _default_companion_id(owner_id: str, device_id: str) -> str:
    """Give each physical Guard a stable, independent companion identity."""
    digest = hashlib.sha256(f"{owner_id}:{device_id}".encode("utf-8")).hexdigest()[:16]
    return f"guard_{digest}"


def _assert_control_only_config(value: dict[str, object]) -> None:
    banned = {"audio", "image", "frame", "face", "embedding", "template", "recording"}
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                if any(token in str(key).lower() for token in banned):
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "guard config may only contain control-plane settings",
                    )
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)


def _validate_policy_config(policy_id: str, config_json: dict[str, object]) -> None:
    try:
        normalize_guard_policy_config(policy_id, config_json)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"invalid guard policy configuration: {exc}",
        ) from exc


def _validate_runtime_config(runtime_config_json: dict[str, object]) -> None:
    try:
        normalize_guard_runtime_config(runtime_config_json)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"invalid guard runtime configuration: {exc}",
        ) from exc


def _binding(row: Any) -> GuardBindingView:
    return GuardBindingView(
        binding_id=row.binding_id,
        owner_id=row.owner_id,
        guard_companion_id=row.guard_companion_id,
        device_id=row.device_id,
        state=row.state,
        policy_id=row.policy_id,
        config_revision=row.config_revision,
        config_json=row.config_json or {},
        runtime_revision=row.runtime_revision,
        runtime_config_json=row.runtime_config_json or {},
        desired_runtime_state=row.desired_runtime_state,
        status_json=row.status_json or {},
        activated_at=row.activated_at,
        disabled_at=row.disabled_at,
        revoked_at=row.revoked_at,
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


async def _owner_face_profile(store: DataStore, row: Any) -> OwnerFaceProfileView:
    references = []
    for reference in await store.owner_face_profiles.list_references(row.profile_revision_id):
        references.append(_owner_face_reference(reference))
    return OwnerFaceProfileView(
        profile_revision_id=row.profile_revision_id,
        profile_id=row.profile_id,
        owner_id=row.owner_id,
        revision=row.revision,
        state=row.state,
        desired_state=row.desired_state,
        model_id=row.model_id,
        preprocessing_version=row.preprocessing_version,
        references=references,
        activated_at=row.activated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _owner_face_reference(reference: Any) -> OwnerFaceReferenceView:
    return OwnerFaceReferenceView(
        reference_id=reference.reference_id,
        pose=reference.pose,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        content_type=reference.content_type,
    )


def _owner_face_delivery(row: Any) -> OwnerFaceDeliveryView:
    return OwnerFaceDeliveryView(
        delivery_id=row.delivery_id,
        binding_id=row.binding_id,
        device_id=row.device_id,
        profile_id=row.profile_id,
        profile_revision=row.profile_revision,
        desired_state=row.desired_state,
        status=row.status,
        command_id=row.command_id,
        attempt_count=row.attempt_count,
        last_error=row.last_error,
        applied_at=row.applied_at,
        updated_at=row.updated_at,
    )
