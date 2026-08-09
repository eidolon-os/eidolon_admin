"""Real Admin + Data V2 + Hub + Kernel process E2E on isolated resources."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

ADMIN_ROOT = Path(__file__).resolve().parents[2]


def _monorepo_root() -> Path:
    common_dir = subprocess.check_output(
        ["git", "-C", str(ADMIN_ROOT), "rev-parse", "--git-common-dir"], text=True
    ).strip()
    return (ADMIN_ROOT / common_dir).resolve().parent.parent


MONOREPO_ROOT = _monorepo_root()
DATA_ROOT = MONOREPO_ROOT / "eidolon_data"
HUB_ROOT = MONOREPO_ROOT / "eidolon_hub"
KERNEL_ROOT = MONOREPO_ROOT / "eidolon_kernel"
ADMIN_PYTHON = ADMIN_ROOT / ".venv/bin/python"
DATA_PYTHON = DATA_ROOT / ".venv/bin/python"
HUB_UVICORN = HUB_ROOT / ".venv/bin/uvicorn"
KERNEL_PYTHON = KERNEL_ROOT / ".venv/bin/python"

DATA_TOKEN = "admin-e2e-data-authority-token-value-0001"
DATA_WORKSPACE_TOKEN = "admin-e2e-workspace-authority-token-value-0001"
LOCAL_API_TOKEN = "admin-e2e-local-api-service-token-value-0001"
HUB_MANAGEMENT_SECRET = "admin-e2e-hub-management-secret-value-0001"
HUB_READER_TOKEN = "admin-e2e-hub-reader-token-value-0001"
HUB_PROVIDER_TOKEN = "admin-e2e-hub-provider-token-value-0001"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()


async def _eventually(
    operation: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    label: str,
    process: subprocess.Popen[str] | None = None,
    timeout: float = 15,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        if process is not None and process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"{label} exited\nstdout={stdout}\nstderr={stderr}")
        try:
            value = operation()
            last = await value if hasattr(value, "__await__") else value
            if predicate(last):
                return last
        except (httpx.HTTPError, OSError) as exc:
            last = exc
        await asyncio.sleep(0.1)
    detail = (
        f"HTTP {last.status_code}: {last.text}"
        if isinstance(last, httpx.Response)
        else repr(last)
    )
    pytest.fail(f"{label} did not become ready; last={detail}")


def _prepare_data(database: Path) -> None:
    subprocess.run(
        [str(DATA_PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=DATA_ROOT,
        env={
            **os.environ,
            "EIDOLON_DATA_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    seed = """
import asyncio
import os
from eidolon_data import DataSettings, DataStore

async def main():
    store = DataStore.open(DataSettings(sqlite_path=os.environ["EIDOLON_DATA_SQLITE_PATH"]))
    try:
        await store.validate_schema()
        await store.owner_commands.create_owner(owner_id="owner-admin-e2e")
        await store.companion_workspaces.provision_workspace(
            owner_id="owner-admin-e2e",
            companion_id="companion-admin-e2e",
            genome_id="genome-admin-e2e",
            realm_id="realm-admin-e2e",
            role="primary",
        )
    finally:
        await store.close()

asyncio.run(main())
"""
    subprocess.run(
        [str(DATA_PYTHON), "-c", seed],
        cwd=DATA_ROOT,
        env={**os.environ, "EIDOLON_DATA_SQLITE_PATH": str(database)},
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_isolated_data_v2(database: Path) -> None:
    expected = {
        "alembic_version",
        "owners",
        "companions",
        "persona_genomes",
        "memory_realms",
        "companion_face_assets",
        "guard_bindings",
        "owner_face_profile_revisions",
        "owner_face_references",
        "audit_outbox",
    }
    with closing(sqlite3.connect(database)) as connection:
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert actual == expected
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not database.with_name(f"{database.name}-wal").exists()
    assert not database.with_name(f"{database.name}-shm").exists()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _management_credential() -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": "admin-e2e-operator",
                "aud": "eidolon-hub",
                "roles": ["hub-admin"],
                "exp": int(time.time()) + 300,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(
        hmac.new(HUB_MANAGEMENT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    )
    return f"Bearer {header}.{payload}.{signature}"


def _spawn(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _enroll(hub_url: str, *, device_id: str, suffix: str) -> None:
    async with httpx.AsyncClient(base_url=hub_url, trust_env=False) as hub:
        response = await hub.post(
            "/api/device-onboarding/v1/enrollments",
            json={
                "operation": "device.enrollment",
                "request_id": f"enroll-{suffix}",
                "retrieval_token": f"device-retrieval-token-{suffix}-00000001",
                "identity": {"device_id": device_id},
                "manifest": {"schema_version": 1, "title": f"Admin E2E {suffix}"},
                "display_name": f"Admin E2E {suffix}",
                "device_kind": "admin-e2e",
            },
        )
    assert response.status_code == 200, response.text


def _workflow(*, device_id: str, request_id: str) -> dict[str, object]:
    return {
        "request_id": request_id,
        "owner_id": "owner-admin-e2e",
        "device_id": device_id,
        "companion_id": "companion-admin-e2e",
        "expected_mount_revision": 0,
        "replace_existing_mount": False,
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _start_admin(
    *, port: int, directory_url: str, services_file: Path
) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "EIDOLON_ADMIN_SERVICES_FILE": str(services_file),
        "EIDOLON_ADMIN_SYSTEM_DIRECTORY_URL": directory_url,
        "EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN": DATA_TOKEN,
        "EIDOLON_ADMIN_DATA_WORKSPACE_AUTHORITY_TOKEN": DATA_WORKSPACE_TOKEN,
        "EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN": LOCAL_API_TOKEN,
        "EIDOLON_ADMIN_API_HOST": "127.0.0.1",
        "EIDOLON_ADMIN_API_PORT": str(port),
    }
    environment.pop("EIDOLON_ADMIN_SYSTEM_DIRECTORY_UDS", None)
    return _spawn(
        [
            str(ADMIN_PYTHON),
            "-m",
            "uvicorn",
            "eidolon_admin_server.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ADMIN_ROOT,
        env=environment,
    )


async def test_real_process_workflow_idempotency_restart_and_data_outage(
    tmp_path: Path,
) -> None:
    required = (ADMIN_PYTHON, DATA_PYTHON, HUB_UVICORN, KERNEL_PYTHON)
    if not all(path.is_file() for path in required):
        pytest.skip("real Admin/Data/Hub/Kernel development runtimes are unavailable")

    data_port, workspace_port, hub_port, kernel_port, directory_port, admin_port = (
        _free_port() for _ in range(6)
    )
    data_url = f"http://127.0.0.1:{data_port}"
    workspace_url = f"http://127.0.0.1:{workspace_port}"
    hub_url = f"http://127.0.0.1:{hub_port}"
    kernel_url = f"http://127.0.0.1:{kernel_port}"
    directory_url = f"http://127.0.0.1:{directory_port}"
    admin_url = f"http://127.0.0.1:{admin_port}"
    data_database = tmp_path / "eidolon-system.sqlite3"
    hub_database = tmp_path / "eidolon-hub.sqlite3"
    kernel_database = tmp_path / "eidolon-kernel.sqlite3"
    hub_settings = tmp_path / "hub.yaml"
    kernel_settings = tmp_path / "kernel.yaml"
    admin_services = tmp_path / "admin-services.yaml"
    hub_settings.write_text(
        f"""onboarding:
  hub_id: eidolon-hub-admin-e2e
  public_base_url: https://hub.admin-e2e.invalid
  retrieval_window_seconds: 1800
discovery:
  mdns:
    enabled: false
channel_provider:
  contract_url: http://127.0.0.1:{_free_port()}/v1
persistence:
  path: {hub_database}
""",
        encoding="utf-8",
    )
    kernel_settings.write_text(
        f"""persistence:
  path: {kernel_database}
system_directory:
  base_url: {directory_url}
  uds_path: null
  timeout_seconds: 2
hub:
  timeout_seconds: 2
companion_authority:
  timeout_seconds: 2
reconciliation:
  interval_seconds: 3600
deployment:
  mode: trusted-local
  trusted_local_ingress: true
""",
        encoding="utf-8",
    )
    admin_services.write_text(
        f"""admin:
  host: 127.0.0.1
  port: {admin_port}
  cors_origins: []
services: []
""",
        encoding="utf-8",
    )
    _prepare_data(data_database)
    _assert_isolated_data_v2(data_database)

    data_process: subprocess.Popen[str] | None = None
    workspace_process: subprocess.Popen[str] | None = None
    hub_process: subprocess.Popen[str] | None = None
    kernel_process: subprocess.Popen[str] | None = None
    directory_process: subprocess.Popen[str] | None = None
    admin_process: subprocess.Popen[str] | None = None
    metrics: dict[str, float | int] = {}
    try:
        data_process = _spawn(
            [
                str(DATA_PYTHON),
                "-m",
                "uvicorn",
                "eidolon_data.api.companion_authority:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(data_port),
                "--log-level",
                "warning",
            ],
            cwd=DATA_ROOT,
            env={
                **os.environ,
                "EIDOLON_DATA_SQLITE_PATH": str(data_database),
                "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN": DATA_TOKEN,
            },
        )
        workspace_process = _spawn(
            [
                str(DATA_PYTHON),
                "-m",
                "uvicorn",
                "eidolon_data.api.workspace_authority:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(workspace_port),
                "--log-level",
                "warning",
            ],
            cwd=DATA_ROOT,
            env={
                **os.environ,
                "EIDOLON_DATA_SQLITE_PATH": str(data_database),
                "EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN": DATA_WORKSPACE_TOKEN,
            },
        )
        hub_process = _spawn(
            [
                str(HUB_UVICORN),
                "hub.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(hub_port),
                "--log-level",
                "warning",
            ],
            cwd=HUB_ROOT,
            env={
                **os.environ,
                "EIDOLON_HUB_SETTINGS_YAML": str(hub_settings),
                "EIDOLON_HUB_MANAGEMENT_JWT_SECRET": HUB_MANAGEMENT_SECRET,
                "EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN": HUB_READER_TOKEN,
                "EIDOLON_HUB_CHANNEL_PROVIDER_TOKEN": HUB_PROVIDER_TOKEN,
            },
        )
        directory_process = _spawn(
            [
                str(ADMIN_PYTHON),
                "-m",
                "uvicorn",
                "tests.control_plane_e2e_support:create_directory_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(directory_port),
                "--log-level",
                "warning",
            ],
            cwd=ADMIN_ROOT / "server",
            env={
                **os.environ,
                "EIDOLON_E2E_DATA_URL": data_url,
                "EIDOLON_E2E_DATA_WORKSPACE_URL": workspace_url,
                "EIDOLON_E2E_HUB_URL": hub_url,
                "EIDOLON_E2E_KERNEL_URL": kernel_url,
            },
        )
        async with httpx.AsyncClient(trust_env=False) as client:
            await _eventually(
                lambda: client.get(f"{data_url}/health"),
                lambda response: response.status_code == 200,
                label="Data",
                process=data_process,
            )
            await _eventually(
                lambda: client.get(f"{workspace_url}/health"),
                lambda response: response.status_code == 200,
                label="Data Workspace",
                process=workspace_process,
            )
            await _eventually(
                lambda: client.get(f"{hub_url}/health"),
                lambda response: response.status_code == 200,
                label="Hub",
                process=hub_process,
            )
            await _eventually(
                lambda: client.get(f"{directory_url}/health"),
                lambda response: response.status_code == 200,
                label="directory",
                process=directory_process,
            )

            kernel_process = _spawn(
                [
                    str(KERNEL_PYTHON),
                    "-m",
                    "uvicorn",
                    "eidolon_kernel.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(kernel_port),
                    "--log-level",
                    "warning",
                ],
                cwd=KERNEL_ROOT,
                env={
                    **os.environ,
                    "EIDOLON_KERNEL_SETTINGS_YAML": str(kernel_settings),
                    "EIDOLON_KERNEL_HUB_MANAGEMENT_TOKEN": HUB_READER_TOKEN,
                    "EIDOLON_KERNEL_COMPANION_AUTHORITY_TOKEN": DATA_TOKEN,
                },
            )
            await _eventually(
                lambda: client.get(f"{kernel_url}/health"),
                lambda response: response.status_code == 200,
                label="Kernel",
                process=kernel_process,
            )
            await _enroll(hub_url, device_id="device-admin-e2e", suffix="primary")

            admin_process = _start_admin(
                port=admin_port,
                directory_url=directory_url,
                services_file=admin_services,
            )
            await _eventually(
                lambda: client.get(f"{admin_url}/api/control-plane/v1/capabilities"),
                lambda response: response.status_code == 200,
                label="Admin",
                process=admin_process,
            )
            workspace_operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
            workspace_path = (
                f"{admin_url}/api/control-plane/v1/workspace-onboarding/operations/"
                f"{workspace_operation_id}"
            )
            workspace_headers = {
                "Authorization": f"Bearer {LOCAL_API_TOKEN}",
            }
            runtime_path = (
                f"{admin_url}/api/control-plane/v1/owners/owner-admin-e2e/"
                "primary-runtime-snapshot"
            )
            missing_runtime_auth = await client.get(runtime_path)
            assert missing_runtime_auth.status_code == 401

            async def timed_runtime() -> tuple[httpx.Response, float]:
                call_started = time.perf_counter()
                response = await client.get(runtime_path, headers=workspace_headers)
                return response, (time.perf_counter() - call_started) * 1000

            runtime_started = time.perf_counter()
            runtime_results = await asyncio.gather(
                *(timed_runtime() for _ in range(20))
            )
            metrics["runtime_read_concurrency"] = len(runtime_results)
            metrics["runtime_read_wall_ms"] = (
                time.perf_counter() - runtime_started
            ) * 1000
            runtime_latencies = [latency for _, latency in runtime_results]
            metrics["runtime_read_p50_ms"] = _percentile(runtime_latencies, 0.50)
            metrics["runtime_read_p95_ms"] = _percentile(runtime_latencies, 0.95)
            runtime_reads = [response for response, _ in runtime_results]
            assert {item.status_code for item in runtime_reads} == {200}
            runtime_snapshot = runtime_reads[0].json()
            assert runtime_snapshot["owner_id"] == "owner-admin-e2e"
            assert runtime_snapshot["companion_id"] == "companion-admin-e2e"
            assert runtime_snapshot["persona_genome"]["genome_id"] == (
                "genome-admin-e2e"
            )
            assert runtime_snapshot["memory_realm"]["realm_id"] == "realm-admin-e2e"
            assert all(item.json() == runtime_snapshot for item in runtime_reads)

            workspace_payload = {
                "owner_display_name": "Admin E2E Owner",
                "companion_display_name": "Admin E2E Companion",
            }
            missing_workspace_auth = await client.put(
                workspace_path,
                json=workspace_payload,
            )
            assert missing_workspace_auth.status_code == 401
            workspace_started = time.perf_counter()
            first_workspace = await client.put(
                workspace_path,
                headers=workspace_headers,
                json=workspace_payload,
            )
            metrics["workspace_first_mutation_ms"] = (
                time.perf_counter() - workspace_started
            ) * 1000
            assert first_workspace.status_code == 200, first_workspace.text

            workspace_duplicates = await asyncio.gather(
                *(
                    client.put(
                        workspace_path,
                        headers=workspace_headers,
                        json=workspace_payload,
                    )
                    for _ in range(6)
                )
            )
            assert {item.status_code for item in workspace_duplicates} == {200}
            assert all(
                item.json() == first_workspace.json() for item in workspace_duplicates
            )
            workspace_conflict = await client.put(
                workspace_path,
                headers=workspace_headers,
                json={**workspace_payload, "companion_display_name": "Different"},
            )
            assert workspace_conflict.status_code == 409

            headers = {"Authorization": _management_credential()}
            payload = _workflow(
                device_id="device-admin-e2e", request_id="admin-e2e-workflow-1"
            )
            started = time.perf_counter()
            first = await client.post(
                f"{admin_url}/api/control-plane/v1/workflows/device-admission",
                headers=headers,
                json=payload,
            )
            metrics["first_mutation_ms"] = (time.perf_counter() - started) * 1000
            assert first.status_code == 200, first.text
            assert first.json()["completed_stage"] == "companion_attached"

            async def timed_post() -> tuple[httpx.Response, float]:
                call_started = time.perf_counter()
                response = await client.post(
                    f"{admin_url}/api/control-plane/v1/workflows/device-admission",
                    headers=headers,
                    json=payload,
                )
                return response, (time.perf_counter() - call_started) * 1000

            duplicate_results = await asyncio.gather(*(timed_post() for _ in range(6)))
            duplicates = [response for response, _ in duplicate_results]
            duplicate_latencies = [latency for _, latency in duplicate_results]
            metrics["duplicate_mutation_concurrency"] = len(duplicates)
            metrics["duplicate_mutation_p50_ms"] = _percentile(
                duplicate_latencies, 0.50
            )
            metrics["duplicate_mutation_p95_ms"] = _percentile(
                duplicate_latencies, 0.95
            )
            assert {response.status_code for response in duplicates} == {200}
            assert {
                response.json()["steps"][1]["state"] for response in duplicates
            } == {"replayed"}

            async def timed_inventory() -> tuple[httpx.Response, float]:
                call_started = time.perf_counter()
                response = await client.get(
                    f"{admin_url}/api/control-plane/v1/owners/owner-admin-e2e/inventory",
                    headers=headers,
                )
                return response, (time.perf_counter() - call_started) * 1000

            inventory_started = time.perf_counter()
            inventory_results = await asyncio.gather(
                *(timed_inventory() for _ in range(20))
            )
            inventory_wall_ms = (time.perf_counter() - inventory_started) * 1000
            inventory_latencies = [latency for _, latency in inventory_results]
            metrics["inventory_concurrency"] = len(inventory_results)
            metrics["inventory_wall_ms"] = inventory_wall_ms
            metrics["inventory_p50_ms"] = _percentile(inventory_latencies, 0.50)
            metrics["inventory_p95_ms"] = _percentile(inventory_latencies, 0.95)
            assert {response.status_code for response, _ in inventory_results} == {200}

            _stop(admin_process)
            admin_process = _start_admin(
                port=admin_port,
                directory_url=directory_url,
                services_file=admin_services,
            )
            await _eventually(
                lambda: client.get(f"{admin_url}/api/control-plane/v1/capabilities"),
                lambda response: response.status_code == 200,
                label="restarted Admin",
                process=admin_process,
            )
            after_restart = await client.post(
                f"{admin_url}/api/control-plane/v1/workflows/device-admission",
                headers=headers,
                json=payload,
            )
            assert after_restart.status_code == 200, after_restart.text
            assert [step["state"] for step in after_restart.json()["steps"][1:]] == [
                "replayed",
                "replayed",
            ]
            workspace_after_restart = await client.get(
                workspace_path,
                headers=workspace_headers,
            )
            assert workspace_after_restart.status_code == 200
            assert workspace_after_restart.json() == first_workspace.json()
            runtime_after_restart = await client.get(
                runtime_path,
                headers=workspace_headers,
            )
            assert runtime_after_restart.status_code == 200
            assert runtime_after_restart.json() == runtime_snapshot

            reused_with_different_payload = payload | {
                "companion_id": "companion-different"
            }
            conflict = await client.post(
                f"{admin_url}/api/control-plane/v1/workflows/device-admission",
                headers=headers,
                json=reused_with_different_payload,
            )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["outcome"] == "blocked"
            assert conflict.json()["recovery"] == "operator-action-required"
            persisted_mount = await client.get(
                f"{kernel_url}/api/kernel/v1/device-mounts/devices/device-admin-e2e",
                headers={"X-Eidolon-Owner": "owner-admin-e2e"},
            )
            assert persisted_mount.status_code == 200, persisted_mount.text
            assert persisted_mount.json()["attached_companion_id"] == (
                "companion-admin-e2e"
            )
            assert persisted_mount.json()["revision"] == 2

            old_route = await client.get(f"{admin_url}/api/data/owners")
            assert old_route.status_code == 404

            await _enroll(hub_url, device_id="device-data-outage", suffix="outage")
            _stop(data_process)
            data_process = None
            outage = await client.post(
                f"{admin_url}/api/control-plane/v1/workflows/device-admission",
                headers=headers,
                json=_workflow(
                    device_id="device-data-outage",
                    request_id="admin-e2e-workflow-data-outage",
                ),
            )
            assert outage.status_code == 202, outage.text
            assert outage.json()["completed_stage"] == "kernel_mounted"
            failure = outage.json()["steps"][-1]["failure"]
            assert failure["kind"] == "upstream_failure"
            assert failure["retryable"] is True
            unavailable_runtime = await client.get(
                runtime_path,
                headers=workspace_headers,
            )
            assert unavailable_runtime.status_code == 503
            assert unavailable_runtime.json()["detail"]["kind"] == "unavailable"

            _stop(workspace_process)
            workspace_process = None
            unavailable_workspace = await client.put(
                (
                    f"{admin_url}/api/control-plane/v1/workspace-onboarding/operations/"
                    "7cab9151-c46a-4c90-a523-7e140ce49225"
                ),
                headers=workspace_headers,
                json=workspace_payload,
            )
            assert unavailable_workspace.status_code == 503
            assert unavailable_workspace.json()["detail"]["kind"] == "unavailable"
    finally:
        _stop(admin_process)
        _stop(kernel_process)
        _stop(directory_process)
        _stop(hub_process)
        _stop(workspace_process)
        _stop(data_process)

    _assert_isolated_data_v2(data_database)
    print("ADMIN_CONTROL_PLANE_E2E_METRICS=" + json.dumps(metrics, sort_keys=True))
