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
from eidolon_sdk.device_foundation.v1 import (
    ClaimPage,
    EnrollmentProposalPage,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    DeviceRef,
    KernelMount,
    KernelMountPage,
    KernelMutationResult,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.app.control_plane.workspace_policy import (
    workspace_request_fingerprint,
)

from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id

# Tests name the device they mean; the name becomes a real device
# instance id, which is a digest of a key and never a chosen string.
_DEVICE_1 = named_device_instance_id("device-1")

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
        device_id=_DEVICE_1,
        owner_id="owner-1",
        device_ref=DeviceRef(
            device_instance_id=_DEVICE_1,
            owner_domain_id="owner-1",
            owner_domain_generation=1,
            claim_generation=1,
            trust_epoch=1,
        ),
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


def test_data_consumed_identity_matches_the_producer_it_was_verified_against() -> None:
    """Verified against Data 48dcb41, which split one role column into three axes.

    The previous baselines were 2a33894 and 718b2cb. Moving the pin is the
    deliberate part: this copy of the contract is closed, so an addition at Data
    arrives here as a decision rather than as an unnoticed field.

    Read this together with the working-tree check below. A pin proves "we were
    right about what we reviewed"; it cannot notice the producer moving on, and
    it did not — Data grew ``kind`` and ``revision``, this test stayed green
    against 718b2cb, and every Admin read of a Companion was failing to parse.
    """

    schema_path = "eidolon_data/contracts/schemas/companion/identity.schema.json"
    schema = _json_at_commit(DATA_ROOT, "48dcb41", schema_path)
    document = CompanionIdentity(
        operation="companion.identity",
        companion_id="companion-1",
        owner_id="owner-1",
        display_name="小忆",
        lifecycle_state="active",
        kind="standard",
        revision=3,
    ).model_dump(mode="json")
    jsonschema.Draft202012Validator(schema).validate(document)

    producer_source = _at_commit(
        DATA_ROOT, "48dcb41", "eidolon_data/api/companion_authority.py"
    )
    assert "/api/companion-authority/v1/companions/{companion_id}" in producer_source
    assert "response_model=CompanionIdentityResponse" in producer_source


def test_the_consumed_identity_still_parses_what_data_publishes_today() -> None:
    """The check the pinned one structurally cannot be: against the working tree.

    A pinned test fails when *this* repository changes. Nothing in it fails when
    the *producer* changes, which is how ``kind`` and ``revision`` reached a
    deployed Data while Admin still rejected them as extra fields. This reads
    the schema as it is on disk right now and asserts the strict consumer
    accepts every document it describes — including each lifecycle value, since
    a consumer that parses only the happy state is a consumer that breaks the
    first time an Owner archives something.
    """

    schema_file = (
        DATA_ROOT / "eidolon_data/contracts/schemas/companion/identity.schema.json"
    )
    if not schema_file.is_file():
        pytest.skip("eidolon_data sibling checkout is unavailable")
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    properties = set(schema["properties"])

    # Every field the producer publishes must be a field this model names.
    # Strict parsing turns an unnamed one into a hard failure, so "we do not
    # need it yet" is not an option available here.
    named = set(CompanionIdentity.model_fields)
    assert properties <= named, f"producer sends fields this model rejects: {properties - named}"

    for state in schema["properties"]["lifecycle_state"]["enum"]:
        identity = CompanionIdentity.model_validate(
            {
                "operation": "companion.identity",
                "companion_id": "companion-1",
                "owner_id": "owner-1",
                "display_name": "小忆",
                "lifecycle_state": state,
                "kind": "standard",
                "revision": 3,
            }
        )
        assert identity.lifecycle_state == state

    # A kind this Admin has never heard of is not a parse failure. The set of
    # product types is the producer's to grow, and an identity that is readable
    # in every other respect must stay readable.
    unknown_kind = CompanionIdentity.model_validate(
        {
            "operation": "companion.identity",
            "companion_id": "companion-1",
            "owner_id": "owner-1",
            "lifecycle_state": "active",
            "kind": "a-kind-from-a-later-release",
            "revision": 1,
        }
    )
    assert unknown_kind.kind == "a-kind-from-a-later-release"


# Deleted here: a test asserting that an identity from a Data predating
# ``display_name`` still parses. It cannot hold now that ``kind`` and
# ``revision`` are required, and under the plan's no-compatibility premise
# (§1.3) it should not: Data and Admin are one Host release and are installed
# together, so tolerating an older producer buys nothing and hides a mismatch.
# Version skew is real between a *client* and a Host, and it is handled at the
# management ABI by the capabilities map — not by loosening internal contracts.


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
def test_kernel_consumed_response_matches_current_producer_contract(
    schema_name: str, document: dict
) -> None:
    prefix = "eidolon_kernel/contracts/schemas/device-mount"
    schema = json.loads((KERNEL_ROOT / prefix / schema_name).read_text(encoding="utf-8"))
    store: dict[str, dict] = {}
    for name in (
        "mount.schema.json",
        "page.schema.json",
        "mutation-result.schema.json",
    ):
        candidate = json.loads((KERNEL_ROOT / prefix / name).read_text(encoding="utf-8"))
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


def test_current_hub_consumes_the_exact_sdk_admission_bindings() -> None:
    source = (HUB_ROOT / "hub/contracts/bindings/admission.py").read_text(
        encoding="utf-8"
    )
    assert "from eidolon_sdk.device_foundation.v1 import" in source
    assert "EnrollmentProposalPage" in source
    assert "ClaimPage" in source
    domain = OwnerDomainId("owner-domain-a")
    assert EnrollmentProposalPage(
        owner_domain_id=domain, items=(), next_cursor=None, observed_at=datetime.now(UTC)
    ).owner_domain_id == domain
    assert ClaimPage(
        owner_domain_id=domain, items=(), next_cursor=None, observed_at=datetime.now(UTC)
    ).owner_domain_id == domain


def test_admin_operator_tree_has_only_its_bounded_removal_intent_database() -> None:
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
                    if needle == "import sqlite3" and path.name in {
                        "removal_intents.py",
                        "admission_intents.py",
                    }:
                        continue
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
        "onboarding_router",
        "resolve_router",
    ):
        assert removed not in main_source


def test_mission_control_is_registered_only_while_it_stays_second_hand() -> None:
    """Why one name left this list, and the condition it left on.

    Every other entry was removed because its subject moved: Data owns owners
    and companions, Hub owns devices and their events, Memory owns
    recollections. Serving those from here again would give a Host two answers
    to one question and no way to say which is true. That rule is unchanged.

    Mission Control is not an authority for anything — it is a read-only view
    that composes what the authorities answer. It was removed for a different
    reason: it opened the product database directly, which is what
    ``test_admin_operator_tree_has_no_database_or_orm_dependency`` forbids, and
    why it could not simply be checked back out. It now asks the same HTTP
    clients every other surface here asks, so the reason no longer describes
    it — and this test holds it to that.
    """

    # Whether it is reachable is asserted by a request in
    # test_control_plane_component.py: this FastAPI defers route
    # materialisation, so neither a source grep nor a walk of app.routes can
    # tell. What is checked here is the condition it was let back in on.
    mission_control = ADMIN_ROOT / "server/eidolon_admin_server/app/mission_control"
    for path in mission_control.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("import eidolon_data", "from eidolon_data", "sqlalchemy"):
            assert forbidden not in text, f"{path.name} went back to the database"


def test_every_controller_command_checks_the_owner_and_the_scope_it_needs() -> None:
    """One rule, declared once, that a new command cannot be added without.

    Each Controller command used to restate the same two-line invariant in its
    own validator, naming its required scope inline. Three copies agreed, which
    is what a rule looks like right up until one of them does not: reads were
    minted with `device.read` alone while the Decision carried
    `device.claim.approve`, so an ActorRef described the request rather than the
    principal, and the pending-device queue — which only an approver may read —
    answered 403 on the Host while every test passed.
    """

    import inspect

    from eidolon_admin_server.app.control_plane import contracts

    commands = [
        value
        for _name, value in inspect.getmembers(contracts, inspect.isclass)
        if issubclass(value, contracts.ControllerCommand)
        and value is not contracts.ControllerCommand
    ]
    assert {command.__name__ for command in commands} == {
        "ControllerCommissioningVoucherRequest",
        "ControllerEnrollmentQuery",
        "ControllerEnrollmentRecoveryQuery",
        "ControllerClaimQuery",
        "ControllerEnrollmentDecisionIntent",
    }
    for command in commands:
        # Declared, not restated: the scope lives on the command that needs it.
        assert command.required_scope in {"device.read", "device.claim.approve"}
        # And the check itself is inherited, never re-implemented.
        assert "_authority" not in vars(command)
