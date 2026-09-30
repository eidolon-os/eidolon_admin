"""The Owner's memory, end to end: phone-facing API → Admin → a real memory realm.

Every seam between a person's phone and their memory is crossed for real here:

- the public Local API routes, with an authenticated Controller session;
- the Admin internal management ABI, reached over HTTP with its credential;
- the control-plane memory client, discovering the realm and calling it;
- the memory realm's production Owner handlers, running as a separate process
  in the ``eidolon_memory`` environment, answering through the shared contract.

Storage there is the fake backend and the worker is a stand-in that applies a
forget after a short delay (see ``eidolon_memory/tests/harness``); nothing
between the phone and the handler is faked. Every public answer is validated
against the committed OpenAPI document the mobile client is generated from, so a
pass here means the phone can parse what the Host actually sends.

This file exists because each repository's own tests were green on 2026-09-22
while "forget" failed on every real match: each side faked the other, and the
fakes agreed with each other rather than with the code.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator

from eidolon_admin_server.app.control_plane.clients import MemoryRecollectionsClient
from eidolon_admin_server.app.control_plane.failure_handler import (
    install_authority_failure_handler,
)
from eidolon_admin_server.app.management.router import router as management_router
from eidolon_admin_server.local_api.management.backend import AdminManagementClient
from tests.test_management_roster import _app, _authenticate, _stub_controller

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

_ROOT = Path(__file__).resolve().parents[3]
_MEMORY = _ROOT / "eidolon_memory"
_HARNESS = _MEMORY / "tests" / "harness" / "owner_surface_server.py"
_PYTHON = _MEMORY / ".venv" / "bin" / "python"
_CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "contracts/management/v1/management-v1.openapi.json"
    ).read_text(encoding="utf-8")
)
_MEMORY_TOKEN = "memory-e2e-token"
_LOCAL_API_TOKEN = "local-api-e2e-token"
_DAY = "2026-09-21T12:00:00+00:00"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def realm(tmp_path: Path):
    if not _PYTHON.exists() or not _HARNESS.exists():
        pytest.skip(
            "needs the eidolon_memory checkout beside this one, with its environment "
            "(run `uv sync` there)"
        )
    port = _free_port()
    process = subprocess.Popen(
        [
            str(_PYTHON),
            str(_HARNESS),
            "--port",
            str(port),
            "--token",
            _MEMORY_TOKEN,
            "--state",
            str(tmp_path),
        ],
        cwd=_MEMORY,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            if httpx.get(f"{base}/api/discovery/agent-routing", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        if process.poll() is not None or time.monotonic() > deadline:
            output = process.stdout.read().decode() if process.stdout else ""
            process.kill()
            pytest.fail(f"memory realm did not start:\n{output}")
        time.sleep(0.2)
    try:
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _phone_facing_app(tmp_path: Path, realm_url: str):
    memory = MemoryRecollectionsClient(
        discovery_url=realm_url,
        client=httpx.AsyncClient(),
        timeout_seconds=10.0,
        service_token=_MEMORY_TOKEN,
    )
    internal = FastAPI()
    internal.state.settings = SimpleNamespace(local_api_service_token=_LOCAL_API_TOKEN)
    internal.state.control_plane = SimpleNamespace(memory=memory)
    install_authority_failure_handler(internal)
    internal.include_router(management_router, prefix="/api")
    backend = AdminManagementClient(
        base_url="http://admin.internal",
        service_token=_LOCAL_API_TOKEN,
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=internal)),
        timeout_seconds=10.0,
    )
    return _app(tmp_path, backend)


def _conforms(method: str, path: str, response: httpx.Response) -> dict:
    """The answer the phone receives, checked against what its client was generated from."""

    assert response.status_code == 200, response.text
    body = response.json()
    schema = _CONTRACT["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator({**schema, "components": _CONTRACT["components"]}).validate(body)
    return body


async def _get(client: httpx.AsyncClient, headers: dict, path: str, **params) -> dict:
    return _conforms("get", path, await client.get(path, params=params, headers=headers))


async def _post(client: httpx.AsyncClient, headers: dict, path: str, body: dict) -> dict:
    return _conforms("post", path, await client.post(path, json=body, headers=headers))


LIBRARY = "/api/management/v1/memory/library"
ENTRIES = "/api/management/v1/memory/entries"
RECOLLECTIONS = "/api/management/v1/memory/recollections"
PREVIEW = "/api/management/v1/memory/forget/preview"
CONFIRM = "/api/management/v1/memory/forget/confirm"
STATUS = "/api/management/v1/memory/forget/status"


async def test_the_owners_memory_is_theirs_to_read_search_and_page(
    tmp_path, monkeypatch, realm
) -> None:
    """Every audience in the Owner's view; one Companion's in its own.

    Before 2026-09-23 the Owner view was the Owner layer only — near empty,
    since every turn is written to one Companion — and an Owner search without
    a Companion answered 503.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_phone_facing_app(tmp_path, realm))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)

        mine = await _get(client, headers, LIBRARY)
        tea_drinkers = await _get(client, headers, LIBRARY, companion_id="c_b")
        found = await _get(client, headers, RECOLLECTIONS, q="工资")

        seen: list[str] = []
        pages = 0
        params: dict[str, object] = {"since": _DAY, "limit": 3}
        while True:
            day = await _get(client, headers, ENTRIES, **params)
            pages += 1
            seen.extend(entry["entry_id"] for entry in day["entries"])
            if not day["more_in_window"]:
                break
            params = {**params, "cursor": day["next_cursor"]}

    assert mine["audience_scope"] == "owner"
    assert mine["entry_count"] == 8
    assert "memory_realm_id" not in mine and "materialization" not in mine
    # Owner layer (two) plus what c_b was told (one); nothing c_a was told.
    assert tea_drinkers["entry_count"] == 3
    assert {item["text"] for item in found["recollections"]} >= {"工资是两万", "工资涨到三万了"}
    # Four of the walks share a timestamp; paging by time alone would skip them.
    assert len(seen) == len(set(seen)) == 8
    assert pages == 3


async def test_forgetting_is_shown_bound_confirmed_once_and_seen_to_finish(
    tmp_path, monkeypatch, realm
) -> None:
    """The flow that failed on 2026-09-22, through every layer.

    Preview lists only what the confirm can remove; the confirm names its
    change; a second confirm is the same change; the phone can ask until the
    change is applied; and afterwards the memory is gone from the library.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_phone_facing_app(tmp_path, realm))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)

        short = await _post(client, headers, PREVIEW, {"target": "茶"})
        preview = await _post(client, headers, PREVIEW, {"target": "工资"})
        first = await _post(
            client, headers, CONFIRM, {"confirmation_token": preview["confirmation_token"]}
        )
        again = await _post(
            client, headers, CONFIRM, {"confirmation_token": preview["confirmation_token"]}
        )

        statuses = [first["status"]]
        deadline = time.monotonic() + 10
        while statuses[-1] not in {"applied", "failed"} and time.monotonic() < deadline:
            progress = await _get(client, headers, STATUS, request_id=first["request_id"])
            statuses.append(progress["status"])
            if statuses[-1] not in {"applied", "failed"}:
                await asyncio.sleep(0.2)

        after = await _post(client, headers, PREVIEW, {"target": "工资"})
        library = await _get(client, headers, LIBRARY)

    # One character matched nothing because it was refused, and says so.
    assert short["status"] == "too_broad"
    assert preview["status"] == "preview"
    # The legacy drawer has no ledger assertion, so the confirm could not remove
    # it; it is not offered.
    assert sorted(entry["entry_id"] for entry in preview["entries"]) == [
        "drawer_salary",
        "drawer_salary_a",
    ]
    assert preview["needs_confirmation"] is True
    assert preview["expires_at"]
    assert "action" not in preview
    assert first["status"] == "accepted"
    assert again["request_id"] == first["request_id"]
    assert statuses[-1] == "applied", statuses
    assert after["status"] == "not_found"
    assert library["entry_count"] == 6
