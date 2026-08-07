"""Verify Admin's consumed models against producer source at pinned commits."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
import yaml
from referencing import Registry, Resource

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMount,
    KernelMountPage,
    KernelMutationResult,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.app.control_plane.workspace_policy import (
    workspace_request_fingerprint,
)

pytestmark = pytest.mark.contract

ADMIN_ROOT = Path(__file__).resolve().parents[2]


def _monorepo_root() -> Path:
    common_dir = subprocess.check_output(
        ["git", "-C", str(ADMIN_ROOT), "rev-parse", "--git-common-dir"],
        text=True,
    ).strip()
    resolved = (ADMIN_ROOT / common_dir).resolve()
    return resolved.parent.parent


MONOREPO_ROOT = _monorepo_root()
DATA_ROOT = MONOREPO_ROOT / "eidolon_data"
KERNEL_ROOT = MONOREPO_ROOT / "eidolon_kernel"
HUB_ROOT = MONOREPO_ROOT / "eidolon_hub"
DATA_WORKSPACE_COMMIT = "9fc4f4e6bcad1e4e44f0a63b7619ce0702031e4e"
KERNEL_WORKSPACE_COMMIT = "c711238ef0be8f87bcf79fec8920b4c3b5cc849c"


def _at_commit(repo: Path, commit: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"], text=True
    )


def _json_at_commit(repo: Path, commit: str, path: str) -> dict:
    return json.loads(_at_commit(repo, commit, path))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _mount() -> KernelMount:
    return KernelMount(
        operation="kernel.device-mount",
        device_id="device-1",
        owner_id="owner-1",
        attached_companion_id=None,
        revision=1,
        created_at=_now(),
        updated_at=_now(),
        request_id="request-1",
        fingerprint="sha256:" + "1" * 64,
        active=True,
    )


def _registry(documents: list[dict]) -> Registry:
    return Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )


def _schema_documents(schema_root: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in schema_root.rglob("*.schema.json")
    ]


def test_data_v2_consumed_identity_matches_baseline_2a33894() -> None:
    schema_path = "eidolon_data/contracts/schemas/companion/identity.schema.json"
    schema = _json_at_commit(DATA_ROOT, "2a33894", schema_path)
    document = CompanionIdentity(
        operation="companion.identity",
        companion_id="companion-1",
        owner_id="owner-1",
        lifecycle_state="active",
    ).model_dump(mode="json")
    jsonschema.Draft202012Validator(schema).validate(document)

    producer_source = _at_commit(
        DATA_ROOT, "2a33894", "eidolon_data/api/companion_authority.py"
    )
    assert "/api/companion-authority/v1/companions/{companion_id}" in producer_source
    assert "response_model=CompanionIdentityResponse" in producer_source


def test_data_workspace_consumed_contract_matches_9fc4f4e() -> None:
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    payload = WorkspaceInitializeRequest(owner_display_name="Manson")
    document = WorkspaceOperation(
        contract_version="1",
        operation="owner-workspace.initialize",
        operation_id=operation_id,
        request_fingerprint=workspace_request_fingerprint(payload),
        status="succeeded",
        owner={
            "owner_id": "owner_32c421a3e0df40f98f7568745ae39d81",
            "display_name": "Manson",
            "lifecycle_state": "active",
        },
        workspace={
            "state": "ready",
            "primary_companion_id": "c_32c421a3e0df40f98f7568745ae39d81",
            "persona_genome_id": "g_32c421a3e0df40f98f7568745ae39d81_origin",
            "memory_realm_id": "r_32c421a3e0df40f98f7568745ae39d81",
        },
    ).model_dump(mode="json")
    schema = _json_at_commit(
        DATA_ROOT,
        DATA_WORKSPACE_COMMIT,
        "eidolon_data/contracts/schemas/workspace/onboarding-operation.schema.json",
    )
    jsonschema.Draft202012Validator(schema).validate(document)

    producer_source = _at_commit(
        DATA_ROOT,
        DATA_WORKSPACE_COMMIT,
        "eidolon_data/api/workspace_authority.py",
    )
    assert (
        producer_source.count('"/api/workspace-authority/v1/operations/{operation_id}"')
        == 2
    )
    assert "EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN" in producer_source
    assert "response_model=WorkspaceOperationResponse" in producer_source


def test_kernel_publishes_workspace_authority_at_c711238() -> None:
    manifest = yaml.safe_load(
        _at_commit(
            KERNEL_ROOT,
            KERNEL_WORKSPACE_COMMIT,
            "config/system-services.yaml",
        )
    )
    workspace = next(
        item for item in manifest["services"] if item["service_id"] == "data-workspace"
    )
    assert workspace["required"] is True
    assert workspace["host_targets"] == {"supervisord": "data:data-workspace-api"}
    assert workspace["endpoints"] == [
        {
            "endpoint_id": "workspace-authority.http",
            "protocol": "http",
            "address": "http://127.0.0.1:8085",
            "contract": (
                "https://eidolon.live/contracts/system-data/workspace/"
                "onboarding-operation-v1.schema.json"
            ),
            "health_url": "http://127.0.0.1:8085/health",
        }
    ]


@pytest.mark.parametrize(
    ("schema_name", "document"),
    [
        ("mount.schema.json", _mount().model_dump(mode="json")),
        (
            "page.schema.json",
            KernelMountPage(
                operation="kernel.device-mount-page",
                next_cursor=None,
                mounts=(_mount(),),
            ).model_dump(mode="json"),
        ),
        (
            "mutation-result.schema.json",
            KernelMutationResult(
                operation="kernel.device-mount-mutation-result",
                mount=_mount(),
                audit_position=1,
                replayed=False,
            ).model_dump(mode="json"),
        ),
    ],
)
def test_kernel_consumed_response_matches_baseline_66e61c9(
    schema_name: str, document: dict
) -> None:
    prefix = "eidolon_kernel/contracts/schemas/device-mount"
    schema = _json_at_commit(KERNEL_ROOT, "66e61c9", f"{prefix}/{schema_name}")
    store: dict[str, dict] = {}
    for name in (
        "mount.schema.json",
        "page.schema.json",
        "mutation-result.schema.json",
    ):
        candidate = _json_at_commit(KERNEL_ROOT, "66e61c9", f"{prefix}/{name}")
        store[candidate["$id"]] = candidate
    validator = jsonschema.Draft202012Validator(
        schema, registry=_registry(list(store.values()))
    )
    validator.validate(document)


def test_kernel_routes_at_66e61c9_are_the_only_routes_admin_calls() -> None:
    router_source = _at_commit(
        KERNEL_ROOT, "66e61c9", "eidolon_kernel/interfaces/http/router.py"
    )
    assert 'APIRouter(prefix="/api/kernel/v1"' in router_source
    assert '@router.post("/device-mounts"' in router_source
    assert '"/device-mounts/devices/{device_id}/attachment"' in router_source
    assert '@router.get("/device-mounts"' in router_source


def test_current_hub_consumed_page_and_mutation_match_producer_schemas() -> None:
    schema_root = HUB_ROOT / "hub/contracts/schemas"
    page_schema = json.loads(
        (schema_root / "device/directory-page.schema.json").read_text(encoding="utf-8")
    )
    status_schema = json.loads(
        (schema_root / "device/status.schema.json").read_text(encoding="utf-8")
    )
    page_document = HubDevicePage(
        operation="device.directory-page", next_cursor=None, devices=()
    ).model_dump(mode="json", by_alias=True)
    status_document = HubLifecycleStatus(
        operation="device.lifecycle-status",
        device_id="device-1",
        owner_id="owner-1",
        lifecycle_state="approved",
    ).model_dump(mode="json", by_alias=True)

    registry = _registry(_schema_documents(schema_root))
    jsonschema.Draft202012Validator(page_schema, registry=registry).validate(
        page_document
    )
    jsonschema.Draft202012Validator(status_schema, registry=registry).validate(
        status_document
    )


def test_admin_operator_tree_has_no_database_or_orm_dependency() -> None:
    production_roots = (
        ADMIN_ROOT / "server/eidolon_admin_server/app",
        ADMIN_ROOT / "server/eidolon_admin_server/local_api",
    )
    forbidden = (
        "import eidolon_data",
        "from eidolon_data",
        "import sqlalchemy",
        "from sqlalchemy",
        "import sqlite3",
        "EIDOLON_DATA_SQLITE_PATH",
        "eidolon.sqlite3",
    )
    violations: list[str] = []
    for production in production_roots:
        for path in production.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    violations.append(f"{path.relative_to(ADMIN_ROOT)}: {needle}")
    assert violations == []


def test_local_state_services_do_not_reference_foreign_authority_databases() -> None:
    local_state_roots = (
        ADMIN_ROOT / "server/eidolon_admin_server/audit",
        ADMIN_ROOT / "server/eidolon_admin_server/bootstrap",
    )
    forbidden = (
        "import eidolon_data",
        "from eidolon_data",
        "DataStore",
        "EIDOLON_DATA_SQLITE_PATH",
        "eidolon-system.sqlite3",
        "eidolon.sqlite3",
        "kernel.sqlite3",
        "hub.sqlite3",
    )
    violations: list[str] = []
    for local_state in local_state_roots:
        for path in local_state.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    violations.append(f"{path.relative_to(ADMIN_ROOT)}: {needle}")
    assert violations == []


def test_removed_legacy_route_prefixes_are_not_registered() -> None:
    main_source = (ADMIN_ROOT / "server/eidolon_admin_server/app/main.py").read_text(
        encoding="utf-8"
    )
    for removed in (
        "data_router",
        "devices_router",
        "memory_router",
        "mission_control_router",
        "onboarding_router",
        "resolve_router",
    ):
        assert removed not in main_source
