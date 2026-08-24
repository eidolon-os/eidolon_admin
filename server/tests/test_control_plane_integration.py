"""Admin integration tests across real HTTP adapters and application boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from eidolon_admin_server.app.control_plane.clients import (
    DATA_CONTRACT,
    DATA_WORKSPACE_CONTRACT,
    HUB_CONTRACT,
    KERNEL_CONTRACT,
)
from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import AdminBindConfig, GatewayConfig, Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class ProducerState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.mount: dict | None = None
        self.mount_requests: dict[str, dict] = {}
        self.attach_requests: dict[str, dict] = {}
        self.approval_requests: set[str] = set()
        self.workspace_operations: dict[str, dict] = {}
        self.fail_mounts = 0

    @staticmethod
    def hub_device() -> dict:
        now = datetime.now(UTC).isoformat()
        return {
            "operation": "device.directory-entry",
            "device_id": "device-1",
            "owner_scope": "owner-1",
            "display_name": "Test Device",
            "device_kind": "test",
            "manifest": {
                "schema_version": 1,
                "title": "Test Device",
                "properties": [],
                "actions": [],
                "events": [],
                "media": [],
            },
            "manifest_revision": "sha256:" + "a" * 64,
            "device_ref": {
                "device_instance_id": "device-1",
                "owner_domain_id": "owner-1",
                "owner_domain_generation": 1,
                "claim_generation": 1,
                "trust_epoch": 1,
                "accepted_manifest_digest": "sha256:" + "a" * 64,
            },
            "lifecycle_state": "approved",
            "enrolled_at": now,
            "updated_at": now,
        }

    @staticmethod
    def mounted(request_id: str, *, revision: int, companion_id: str | None) -> dict:
        now = datetime.now(UTC).isoformat()
        return {
            "operation": "kernel.device-mount",
            "device_id": "device-1",
            "owner_id": "owner-1",
            "device_ref": {
                "device_instance_id": "device-1",
                "owner_domain_id": "owner-1",
                "owner_domain_generation": 1,
                "claim_generation": 1,
                "trust_epoch": 1,
                "accepted_manifest_digest": "sha256:" + "a" * 64,
            },
            "attached_companion_id": companion_id,
            "revision": revision,
            "created_at": now,
            "updated_at": now,
            "request_id": request_id,
            "fingerprint": "sha256:" + "c" * 64,
            "active": True,
        }


def producer_app(state: ProducerState) -> FastAPI:
    app = FastAPI()
    endpoints = {
        ("data", "companion-authority.http"): (
            "http://producer.test",
            DATA_CONTRACT,
        ),
        ("data-workspace", "workspace-authority.http"): (
            "http://producer.test",
            DATA_WORKSPACE_CONTRACT,
        ),
        ("hub", "device-authority.http"): ("http://producer.test", HUB_CONTRACT),
        ("kernel", "device-mount.http"): (
            "http://producer.test",
            KERNEL_CONTRACT,
        ),
    }

    @app.get("/api/system/v1/services/{service_id}/endpoints/{endpoint_id}")
    async def resolve(service_id: str, endpoint_id: str):
        value = endpoints.get((service_id, endpoint_id))
        if value is None:
            raise HTTPException(404, "endpoint not ready")
        address, contract = value
        return {
            "operation": "system.service-endpoint",
            "service_id": service_id,
            "endpoint_id": endpoint_id,
            "protocol": "http",
            "address": address,
            "contract": contract,
        }

    @app.get("/api/companion-authority/v1/companions/{companion_id}")
    async def companion(
        companion_id: str, authorization: str = Header(alias="Authorization")
    ):
        if authorization != "Bearer data-token":
            raise HTTPException(401, "bad Data credential")
        if companion_id != "companion-1":
            raise HTTPException(404, "companion not found")
        return {
            "operation": "companion.identity",
            "companion_id": companion_id,
            "owner_id": "owner-1",
            "lifecycle_state": "active",
        }

    def workspace_result(operation_id: str, payload: dict) -> dict:
        marker = operation_id.replace("-", "")
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "contract_version": "1",
            "operation": "owner-workspace.initialize",
            "operation_id": operation_id,
            "request_fingerprint": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            "status": "succeeded",
            "owner": {
                "owner_id": f"owner_{marker}",
                "display_name": payload["owner_display_name"],
                "lifecycle_state": "active",
            },
            "workspace": {
                "state": "ready",
                "primary_companion_id": f"c_{marker}",
                "persona_genome_id": f"g_{marker}_origin",
                "memory_realm_id": f"r_{marker}",
            },
        }

    @app.put("/api/workspace-authority/v1/operations/{operation_id}")
    async def initialize_workspace(
        operation_id: str,
        payload: dict,
        authorization: str = Header(alias="Authorization"),
    ):
        if authorization != "Bearer workspace-token":
            raise HTTPException(403, "bad workspace credential")
        result = workspace_result(operation_id, payload)
        async with state.lock:
            existing = state.workspace_operations.get(operation_id)
            if existing is not None:
                if existing["request_fingerprint"] != result["request_fingerprint"]:
                    raise HTTPException(
                        409, "operation already used for different input"
                    )
                return existing
            state.workspace_operations[operation_id] = result
            return result

    @app.get("/api/workspace-authority/v1/operations/{operation_id}")
    async def get_workspace(
        operation_id: str,
        authorization: str = Header(alias="Authorization"),
    ):
        if authorization != "Bearer workspace-token":
            raise HTTPException(403, "bad workspace credential")
        result = state.workspace_operations.get(operation_id)
        if result is None:
            raise HTTPException(404, "workspace operation not found")
        return result

    @app.get("/api/device-management/v1/owners/{owner_id}/devices")
    async def devices(
        owner_id: str, authorization: str = Header(alias="Authorization")
    ):
        if authorization != "Bearer operator":
            raise HTTPException(403, "management scope denied")
        values = [state.hub_device()] if owner_id == "owner-1" else []
        return {
            "operation": "device.directory-page",
            "next_cursor": None,
            "devices": values,
        }

    @app.post("/api/device-management/v1/devices/{device_id}/approval")
    async def approve(
        device_id: str,
        payload: dict,
        authorization: str = Header(alias="Authorization"),
    ):
        if authorization != "Bearer operator":
            raise HTTPException(403, "management scope denied")
        if device_id != "device-1":
            raise HTTPException(404, "device not found")
        state.approval_requests.add(payload["request_id"])
        return {
            "operation": "device.lifecycle-status",
            "device_id": device_id,
            "owner_id": payload["owner_id"],
            "lifecycle_state": "approved",
        }

    @app.get("/api/kernel/v1/device-mounts")
    async def mounts(x_eidolon_owner: str = Header(alias="X-Eidolon-Owner")):
        values = []
        if state.mount and state.mount["owner_id"] == x_eidolon_owner:
            values.append(state.mount)
        return {
            "operation": "kernel.device-mount-page",
            "next_cursor": None,
            "mounts": values,
        }

    @app.post("/api/kernel/v1/device-mounts")
    async def mount(
        payload: dict, x_eidolon_owner: str = Header(alias="X-Eidolon-Owner")
    ):
        async with state.lock:
            if state.fail_mounts:
                state.fail_mounts -= 1
                raise HTTPException(503, "Kernel storage temporarily unavailable")
            request_id = payload["request_id"]
            if request_id in state.mount_requests:
                return state.mount_requests[request_id] | {"replayed": True}
            state.mount = state.mounted(request_id, revision=1, companion_id=None)
            result = {
                "operation": "kernel.device-mount-mutation-result",
                "mount": state.mount,
                "audit_position": 1,
                "replayed": False,
            }
            state.mount_requests[request_id] = result
            return result

    @app.post("/api/kernel/v1/device-mounts/devices/{device_id}/attachment")
    async def attach(
        device_id: str,
        payload: dict,
        x_eidolon_owner: str = Header(alias="X-Eidolon-Owner"),
    ):
        async with state.lock:
            if not state.mount or state.mount["owner_id"] != x_eidolon_owner:
                raise HTTPException(404, "mount not found")
            if payload["companion_id"] != "companion-1":
                raise HTTPException(503, "Data companion authority unavailable")
            request_id = payload["request_id"]
            if request_id in state.attach_requests:
                return state.attach_requests[request_id] | {"replayed": True}
            state.mount = state.mounted(
                request_id, revision=2, companion_id=payload["companion_id"]
            )
            result = {
                "operation": "kernel.device-mount-mutation-result",
                "mount": state.mount,
                "audit_position": 2,
                "replayed": False,
            }
            state.attach_requests[request_id] = result
            return result

    return app


async def admin_app(
    tmp_path: Path, producer: FastAPI
) -> tuple[FastAPI, httpx.AsyncClient]:
    settings = Settings(
        services_file=tmp_path / "unused.yaml",
        system_directory_url="http://producer.test",
        data_authority_token="data-token",
        data_workspace_authority_token="workspace-token",
        local_api_service_token="local-api-token",
        supervisor_socket=tmp_path / "supervisor.sock",
        supervisor_available_dir=tmp_path,
        supervisor_enabled_dir=tmp_path,
    )
    app = create_app(
        GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]), settings
    )
    await app.state.control_plane.close()
    await app.state.http_client.aclose()
    producer_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=producer), base_url="http://producer.test"
    )
    app.state.http_client = producer_client
    app.state.control_plane = ControlPlaneService.build(
        settings=settings, http_client=producer_client
    )
    return app, producer_client


def workflow_payload() -> dict:
    return {
        "request_id": "workflow-integration-1",
        "owner_id": "owner-1",
        "device_id": "device-1",
        "companion_id": "companion-1",
        "expected_mount_revision": 0,
        "replace_existing_mount": False,
    }


async def call_admin(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        return await client.request(method, path, **kwargs)


async def test_full_http_stack_success_inventory_and_data_read(tmp_path: Path) -> None:
    state = ProducerState()
    app, producer_client = await admin_app(tmp_path, producer_app(state))
    try:
        result = await call_admin(
            app,
            "POST",
            "/api/operator/v1/workflows/device-admission",
            headers={"Authorization": "Bearer operator"},
            json=workflow_payload(),
        )
        inventory = await call_admin(
            app,
            "GET",
            "/api/operator/v1/owners/owner-1/inventory",
            headers={"Authorization": "Bearer operator"},
        )
        companion = await call_admin(
            app,
            "GET",
            "/api/control-plane/v1/companions/companion-1",
            # The internal plane, so this is the caller proving it is the Local
            # API — not the operator credential the two calls above forward to
            # Hub. Same header, different meaning, which is why they are now on
            # different planes.
            headers={"Authorization": "Bearer local-api-token"},
        )
    finally:
        await app.state.control_plane.close()
        await producer_client.aclose()

    assert result.status_code == 200
    assert result.json()["completed_stage"] == "companion_attached"
    assert inventory.status_code == 200
    assert inventory.json()["degraded"] is False
    assert inventory.json()["mounts"][0]["attached_companion_id"] == "companion-1"
    assert companion.json()["lifecycle_state"] == "active"


async def test_workspace_http_stack_is_content_bound_and_converges(
    tmp_path: Path,
) -> None:
    state = ProducerState()
    app, producer_client = await admin_app(tmp_path, producer_app(state))
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    path = f"/api/control-plane/v1/workspace-onboarding/operations/{operation_id}"
    headers = {"Authorization": "Bearer local-api-token"}
    payload = {
        "owner_display_name": "Manson",
        "companion_display_name": "Eidolon",
    }
    try:
        missing_auth = await call_admin(app, "PUT", path, json=payload)
        first, second = await asyncio.gather(
            call_admin(app, "PUT", path, headers=headers, json=payload),
            call_admin(app, "PUT", path, headers=headers, json=payload),
        )
        queried = await call_admin(app, "GET", path, headers=headers)
        conflict = await call_admin(
            app,
            "PUT",
            path,
            headers=headers,
            json={**payload, "companion_display_name": "Different"},
        )
    finally:
        await app.state.control_plane.close()
        await producer_client.aclose()

    assert missing_auth.status_code == 401
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == queried.json()
    assert conflict.status_code == 409
    assert len(state.workspace_operations) == 1


async def test_partial_failure_retry_and_admin_restart_recovery(tmp_path: Path) -> None:
    state = ProducerState()
    state.fail_mounts = 1
    producer = producer_app(state)
    first_app, first_client = await admin_app(tmp_path, producer)
    first = await call_admin(
        first_app,
        "POST",
        "/api/operator/v1/workflows/device-admission",
        headers={"Authorization": "Bearer operator"},
        json=workflow_payload(),
    )
    await first_app.state.control_plane.close()
    await first_client.aclose()

    second_app, second_client = await admin_app(tmp_path, producer)
    try:
        recovered = await call_admin(
            second_app,
            "POST",
            "/api/operator/v1/workflows/device-admission",
            headers={"Authorization": "Bearer operator"},
            json=workflow_payload(),
        )
        replayed = await call_admin(
            second_app,
            "POST",
            "/api/operator/v1/workflows/device-admission",
            headers={"Authorization": "Bearer operator"},
            json=workflow_payload(),
        )
    finally:
        await second_app.state.control_plane.close()
        await second_client.aclose()

    assert first.status_code == 202
    assert first.json()["completed_stage"] == "hub_approved"
    assert recovered.status_code == 200
    assert recovered.json()["completed_stage"] == "companion_attached"
    assert replayed.status_code == 200
    assert replayed.json()["steps"][1]["state"] == "replayed"
    assert replayed.json()["steps"][2]["state"] == "replayed"
    assert len(state.approval_requests) == 1
    assert len(state.mount_requests) == 1
    assert len(state.attach_requests) == 1


async def test_concurrent_duplicate_workflows_converge(tmp_path: Path) -> None:
    state = ProducerState()
    app, producer_client = await admin_app(tmp_path, producer_app(state))
    try:
        first, second = await asyncio.gather(
            *(
                call_admin(
                    app,
                    "POST",
                    "/api/operator/v1/workflows/device-admission",
                    headers={"Authorization": "Bearer operator"},
                    json=workflow_payload(),
                )
                for _ in range(2)
            )
        )
    finally:
        await app.state.control_plane.close()
        await producer_client.aclose()

    assert first.status_code == second.status_code == 200
    assert len(state.mount_requests) == 1
    assert len(state.attach_requests) == 1
    assert state.mount is not None
    assert state.mount["revision"] == 2
