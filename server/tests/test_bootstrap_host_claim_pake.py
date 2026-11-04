"""The Setup code proven by SRP-6a instead of sent (ADR-0008).

What these hold the Host to: the arithmetic is RFC 5054's and the golden vector
is current; the transcript names the keys the endpoint document already told
the phone to pin, so a relay with its own TLS key cannot finish; a handshake is
answered once; a wrong code is refused without closing the window (ADR-0007
stands); and guesses are paced per transport, so a LAN attacker never locks out
the person standing next to the Host.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from eidolon_admin_server.bootstrap import pake
from eidolon_admin_server.bootstrap.adapters.network import InMemoryNetworkProvisioning
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import (
    CommissioningRequestRejected,
    CommissioningService,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.bootstrap.daemon import run_daemon
from eidolon_admin_server.bootstrap.guess_budget import (
    BLE_PACE,
    LAN_PACE,
    LAN_SLOW_AFTER_FAILURES,
    LAN_SLOW_PACE,
    LAN_SOURCE_PACE,
    GuessBudget,
    GuessRefused,
)
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.service import BootstrapService
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from tests.setup_code_claim_support import (
    b64,
    claim_over_lan,
    controller_claim,
    phone_proof,
    prove_over_ble,
    unb64,
)

ADMIN_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ADMIN_ROOT / "contracts/bootstrap/v1"
GOLDEN = json.loads(
    (CONTRACTS / "golden/host-claim-srp6a-v1.json").read_text(encoding="utf-8")
)


def _pake_schema(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "host-claim-pake.schema.json").read_text())
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{name}"},
        registry=Registry().with_resource(schema["$id"], Resource.from_contents(schema)),
        format_checker=FormatChecker(),
    )


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


@pytest.fixture(params=["memory", "sqlite"])
def host(request: pytest.FixtureRequest, tmp_path: Path):
    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if request.param == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
        network=InMemoryNetworkProvisioning(),
    )
    bootstrap.initialize()
    try:
        yield bootstrap, store, bootstrap.issue_setup_code(300)
    finally:
        bootstrap.shutdown()


def _request(operation: str, payload: dict) -> dict:
    return {
        "contract_version": "1",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "operation": operation,
        "payload": payload,
    }


async def _ok(session: CommissioningProtocolSession, operation: str, payload: dict) -> dict:
    answer = await session.handle(_request(operation, payload))
    assert answer["ok"] is True, answer
    return answer["result"]


async def _refused(session: CommissioningProtocolSession, operation: str, payload: dict) -> dict:
    answer = await session.handle(_request(operation, payload))
    assert answer["ok"] is False, answer
    error_schema = json.loads((CONTRACTS / "error.schema.json").read_text())
    Draft202012Validator(error_schema).validate(answer["error"])
    return answer["error"]


def _start_payload(setup: dict, controller: dict) -> dict:
    return {
        "suite": pake.SUITE,
        "commissioning_id": setup["commissioning_id"],
        "controller_public_key": controller["public_key"],
    }


# --- the arithmetic --------------------------------------------------------


def test_the_golden_vector_is_current_and_checks_rfc5054() -> None:
    """The generator re-derives RFC 5054 Appendix B before it writes anything,
    so a pass here is the published vector agreeing with this code too."""

    result = subprocess.run(
        [sys.executable, str(CONTRACTS / "generate_host_claim_srp6a.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_confirmation_keys_are_rfc5869_hkdf() -> None:
    handshake = GOLDEN["handshake"]
    K = bytes.fromhex(handshake["K"])
    transcript = bytes.fromhex(handshake["transcript_hash"])
    for info, expected in (
        (pake.CLIENT_CONFIRM_INFO, handshake["client_confirm_key"]),
        (pake.SERVER_CONFIRM_INFO, handshake["server_confirm_key"]),
    ):
        reference = HKDF(
            algorithm=hashes.SHA512(), length=64, salt=transcript, info=info
        ).derive(K)
        assert reference.hex() == expected


def test_the_golden_host_half_answers_the_goldenphone_proof() -> None:
    handshake = GOLDEN["handshake"]
    exchange = pake.ServerExchange.begin(
        identity=handshake["identity"],
        password=pake.password_for_code(handshake["code"]),
        salt=bytes.fromhex(handshake["salt"]),
        b=int(handshake["b"], 16),
    )
    assert exchange.B_encoded.hex() == handshake["B"]
    assert exchange.v == int(handshake["v"], 16)
    answer = exchange.finish(
        A_encoded=bytes.fromhex(handshake["A"]),
        context=handshake["context_bytes"].encode("ascii"),
        presented_proof=bytes.fromhex(handshake["client_proof"]),
    )
    assert answer.hex() == handshake["server_proof"]
    for refusal in GOLDEN["host_refuses_proof"]:
        with pytest.raises(pake.PakeRejected):
            exchange.finish(
                A_encoded=bytes.fromhex(refusal["A"]),
                context=handshake["context_bytes"].encode("ascii"),
                presented_proof=bytes.fromhex(refusal["client_proof"]),
            )
    for refusal in GOLDEN["both_refuse_public_value"]:
        with pytest.raises(pake.PakeRejected):
            pake.GROUP.public_value(bytes.fromhex(refusal["value"]))


# --- over BLE --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_phone_with_the_code_claims_without_sending_it(host) -> None:
    bootstrap, store, setup = host
    controller = controller_claim()
    session = CommissioningProtocolSession(bootstrap.commissioning)

    start = await _ok(session, "pake.start", _start_payload(setup, controller))
    _pake_schema("start_result").validate(start)
    endpoint = bootstrap.commissioning_endpoint()
    # The context is the endpoint document's own facts, so a phone can check
    # it against what it already pinned before it proves anything.
    assert start["context"]["host_public_key"] == endpoint["host_public_key"]
    assert start["context"]["tls_spki_fingerprint"] == endpoint["tls_spki_fingerprint"]
    assert start["context"]["reset_epoch"] == endpoint["reset_epoch"]
    assert start["context"]["commissioning_id"] == setup["commissioning_id"]
    assert start["context"]["transport"] == "ble"
    assert start["context"]["controller_public_key_sha256"] == b64(
        hashlib.sha256(unb64(controller["public_key"])).digest()
    )

    confirm, expected_server_proof = phone_proof(start, code=setup["setup_code"])
    confirmed = await _ok(session, "pake.confirm", confirm)
    _pake_schema("confirm_result").validate(confirmed)
    assert confirmed["server_proof"] == expected_server_proof

    claimed = await _ok(session, "claim.complete", controller)
    assert claimed["controller"]["controller_id"] == controller["controller_id"]
    assert store.get_state().claim_state.value == "claimed"
    assert setup["setup_code"] not in json.dumps([start, confirm, confirmed])


@pytest.mark.asyncio
async def test_a_claim_must_present_the_key_the_code_was_proven_for(host) -> None:
    bootstrap, store, setup = host
    proven, other = controller_claim(), controller_claim()
    session = CommissioningProtocolSession(bootstrap.commissioning)
    start = await _ok(session, "pake.start", _start_payload(setup, proven))
    confirm, _ = phone_proof(start, code=setup["setup_code"])
    await _ok(session, "pake.confirm", confirm)

    error = await _refused(session, "claim.complete", other)
    assert error["code"] == "controller_denied"
    assert store.get_state().claim_state.value == "unclaimed"
    await _ok(session, "claim.complete", proven)


@pytest.mark.asyncio
async def test_a_wrong_code_is_refused_and_the_window_stays_open(host) -> None:
    bootstrap, store, setup = host
    controller = controller_claim()
    wrong = "00000012" if setup["setup_code"] != "00000012" else "00000013"
    session = CommissioningProtocolSession(bootstrap.commissioning)

    start = await _ok(session, "pake.start", _start_payload(setup, controller))
    confirm, _ = phone_proof(start, code=wrong)
    error = await _refused(session, "pake.confirm", confirm)
    assert error == {
        "code": "commissioning_denied",
        "message": "Commissioning session is unavailable",
        "retryable": False,
    }
    window = store.latest_commissioning_session()
    assert window.failed_attempts == 1
    assert window.revoked_at is None

    start = await _ok(session, "pake.start", _start_payload(setup, controller))
    confirm, _ = phone_proof(start, code=setup["setup_code"])
    await _ok(session, "pake.confirm", confirm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("tls_spki_fingerprint", "sha256:" + b64(b"\x01" * 32)),
        ("host_public_key", b64(b"\x02" * 32)),
        ("reset_epoch", 99),
        ("transport", "lan"),
    ],
)
async def test_a_phone_that_pinned_something_else_cannot_finish(
    host, field: str, value: object
) -> None:
    """The relay case: a phone talking to a stand-in that forwards to the real
    Host has pinned the stand-in's keys. Its proof names them, and the real
    Host refuses it even though the code is right."""

    bootstrap, _, setup = host
    controller = controller_claim()
    session = CommissioningProtocolSession(bootstrap.commissioning)
    start = await _ok(session, "pake.start", _start_payload(setup, controller))
    confirm, _ = phone_proof(start, code=setup["setup_code"], overrides={field: value})
    assert (await _refused(session, "pake.confirm", confirm))["code"] == "commissioning_denied"


@pytest.mark.asyncio
async def test_a_handshake_is_answered_once_and_only_on_its_own_link(host) -> None:
    bootstrap, _, setup = host
    controller = controller_claim()
    first = CommissioningProtocolSession(bootstrap.commissioning)
    second = CommissioningProtocolSession(bootstrap.commissioning)
    start = await _ok(first, "pake.start", _start_payload(setup, controller))
    confirm, _ = phone_proof(start, code=setup["setup_code"])

    assert (await _refused(second, "pake.confirm", confirm))["code"] == "pake_handshake_unknown"
    # The link that started it has not lost it to the other link's attempt.
    await _ok(first, "pake.confirm", confirm)

    third = CommissioningProtocolSession(bootstrap.commissioning)
    start = await _ok(third, "pake.start", _start_payload(setup, controller))
    wrong_then_right, _ = phone_proof(start, code="00000012")
    await _refused(third, "pake.confirm", wrong_then_right)
    right, _ = phone_proof(start, code=setup["setup_code"])
    error = await _refused(third, "pake.confirm", right)
    assert error == {
        "code": "pake_handshake_unknown",
        "message": "Start the Setup code exchange again",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_a_proof_for_a_window_claimed_meanwhile_is_not_honoured(host) -> None:
    """Two phones, one window: the one that finishes second must not be told it
    is in, even with the right code, because the window it proved against is
    gone."""

    bootstrap, store, setup = host
    commissioning = bootstrap.commissioning
    start = commissioning.pake_start(_start_payload(setup, controller_claim()), transport="ble")
    confirm, _ = phone_proof(start, code=setup["setup_code"])

    claim_over_lan(
        bootstrap,
        commissioning_id=setup["commissioning_id"],
        code=setup["setup_code"],
        controller=controller_claim(),
        source="192.168.1.20",
    )

    # Held at the service, not only at the callers that happen to re-check
    # afterwards: a server proof is what tells a phone it reached the Host it
    # meant to, and it must not be handed out for a window that is gone.
    with pytest.raises(CommissioningRequestRejected) as refused:
        commissioning.pake_confirm(confirm, transport="ble")
    assert refused.value.code == "commissioning_denied"
    assert len(store.list_controllers()) == 1


@pytest.mark.asyncio
async def test_an_unknown_suite_and_an_unknown_window_are_named(host) -> None:
    bootstrap, _, setup = host
    controller = controller_claim()
    session = CommissioningProtocolSession(bootstrap.commissioning)
    unknown_suite = {**_start_payload(setup, controller), "suite": "spake2plus-v1"}
    assert (await _refused(session, "pake.start", unknown_suite))["code"] == "unsupported_suite"
    no_window = {
        **_start_payload(setup, controller),
        "commissioning_id": "3f6b8a62-0c1e-4b8e-9a51-2d7c4e9f1a03",
    }
    assert (await _refused(session, "pake.start", no_window))["code"] == "commissioning_denied"


def test_a_half_finished_handshake_expires() -> None:
    now = [1000.0]
    store = InMemoryBootstrapStateStore()
    store.open()
    store.initialize("2026-11-05T00:00:00Z")
    store.issue_commissioning_session(
        session_id="3f6b8a62-0c1e-4b8e-9a51-2d7c4e9f1a03",
        secret_hash=pake.password_for_code("40718256"),
        created_at="2026-11-05T00:00:00Z",
    )
    service = CommissioningService(
        store=store,
        network=InMemoryNetworkProvisioning(),
        host_context=lambda: {
            "host_public_key": b64(b"\x03" * 32),
            "tls_spki_fingerprint": "sha256:" + b64(b"\x04" * 32),
        },
        monotonic=lambda: now[0],
    )
    start = service.pake_start(
        {
            "suite": pake.SUITE,
            "commissioning_id": "3f6b8a62-0c1e-4b8e-9a51-2d7c4e9f1a03",
            "controller_public_key": controller_claim()["public_key"],
        },
        transport="ble",
    )
    confirm, _ = phone_proof(start, code="40718256")
    now[0] += 30.0
    with pytest.raises(CommissioningRequestRejected) as refused:
        service.pake_confirm(confirm, transport="ble")
    assert refused.value.code == "pake_handshake_unknown"


# --- pacing ----------------------------------------------------------------


def test_ble_guesses_are_paced_and_come_back_with_time() -> None:
    now = [0.0]
    budget = GuessBudget(clock=lambda: now[0])
    for _ in range(BLE_PACE.capacity):
        budget.take(window="w", transport="ble")
    with pytest.raises(GuessRefused) as refused:
        budget.take(window="w", transport="ble")
    assert refused.value.retry_after_ms == int(BLE_PACE.period * 1000)
    now[0] += BLE_PACE.period
    budget.take(window="w", transport="ble")


def test_a_lan_flood_never_starves_the_person_at_the_host() -> None:
    now = [0.0]
    budget = GuessBudget(clock=lambda: now[0])
    for index in range(LAN_PACE.capacity):
        budget.take(window="w", transport="lan", source=f"10.0.0.{index}")
    with pytest.raises(GuessRefused):
        budget.take(window="w", transport="lan", source="10.0.0.200")
    budget.take(window="w", transport="ble")


def test_one_lan_source_cannot_spend_the_whole_lan() -> None:
    now = [0.0]
    budget = GuessBudget(clock=lambda: now[0])
    for _ in range(LAN_SOURCE_PACE.capacity):
        budget.take(window="w", transport="lan", source="10.0.0.9")
    with pytest.raises(GuessRefused) as refused:
        budget.take(window="w", transport="lan", source="10.0.0.9")
    assert refused.value.retry_after_ms == int(LAN_SOURCE_PACE.period * 1000)
    # A refusal spent nothing from the shared bucket: everyone else still has
    # exactly what the busy source left them.
    for _ in range(LAN_PACE.capacity - LAN_SOURCE_PACE.capacity):
        budget.take(window="w", transport="lan", source="10.0.0.10")


def test_a_long_lan_attack_slows_the_lan_down() -> None:
    now = [0.0]
    budget = GuessBudget(clock=lambda: now[0])
    budget.take(window="w", transport="lan", source="10.0.0.0")
    for _ in range(LAN_SLOW_AFTER_FAILURES):
        budget.failed(window="w", transport="lan")
    for _ in range(LAN_SLOW_PACE.capacity - 1):
        budget.take(window="w", transport="lan", source=secrets.token_hex(4))
    with pytest.raises(GuessRefused) as refused:
        budget.take(window="w", transport="lan", source="10.0.0.1")
    assert refused.value.retry_after_ms == int(LAN_SLOW_PACE.period * 1000)


def test_a_new_window_starts_with_a_full_budget() -> None:
    now = [0.0]
    budget = GuessBudget(clock=lambda: now[0])
    for _ in range(BLE_PACE.capacity):
        budget.take(window="first", transport="ble")
    with pytest.raises(GuessRefused):
        budget.take(window="first", transport="ble")
    budget.take(window="second", transport="ble")


@pytest.mark.asyncio
async def test_the_host_paces_guesses_and_never_closes_the_window(host) -> None:
    """Running out is a wait on that transport, never a closed window, and the
    LAN is a separate lane from the person standing at the Host."""

    bootstrap, store, setup = host
    wrong = "00000012" if setup["setup_code"] != "00000012" else "00000013"
    controller = controller_claim()
    for _ in range(BLE_PACE.capacity):
        answer = await prove_over_ble(
            CommissioningProtocolSession(bootstrap.commissioning),
            commissioning_id=setup["commissioning_id"],
            code=wrong,
            controller=controller,
        )
        assert answer["error"]["code"] == "commissioning_denied"
    error = await _refused(
        CommissioningProtocolSession(bootstrap.commissioning),
        "pake.start",
        _start_payload(setup, controller),
    )
    assert error["code"] == "rate_limited"
    assert error["retryable"] is True
    assert 0 < error["retry_after_ms"] <= int(BLE_PACE.period * 1000)
    window = store.latest_commissioning_session()
    assert window.failed_attempts == BLE_PACE.capacity
    assert window.revoked_at is None
    claim_over_lan(
        bootstrap,
        commissioning_id=setup["commissioning_id"],
        code=setup["setup_code"],
        controller=controller_claim(),
        source="192.168.1.20",
    )


@pytest.mark.asyncio
async def test_an_unknown_window_spends_nothing(host) -> None:
    bootstrap, _, setup = host
    controller = controller_claim()
    other = {**_start_payload(setup, controller), "commissioning_id": "3f6b8a62-0c1e-4b8e-9a51-2d7c4e9f1a03"}
    for _ in range(BLE_PACE.capacity * 2):
        session = CommissioningProtocolSession(bootstrap.commissioning)
        assert (await _refused(session, "pake.start", other))["code"] == "commissioning_denied"
    session = CommissioningProtocolSession(bootstrap.commissioning)
    await _ok(session, "pake.start", _start_payload(setup, controller))


# --- over the LAN, through bootstrapd and the Local API --------------------


@pytest.fixture
def short_runtime_dir() -> Path:
    import shutil
    import tempfile

    project_runtime = ADMIN_ROOT / "var"
    project_runtime.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="ep-", dir=project_runtime))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_the_lan_claims_by_pake_and_says_so_when_it_will_not(
    tmp_path: Path, short_runtime_dir: Path
) -> None:
    settings = BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=short_runtime_dir,
        control_socket=short_runtime_dir / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        setup_code_ttl_seconds=600,
    )
    stop = asyncio.Event()
    daemon = asyncio.create_task(run_daemon(settings, stop_event=stop))
    try:
        for _ in range(100):
            if settings.control_socket.exists():
                break
            await asyncio.sleep(0.01)
        control = BootstrapControlClient(settings.control_socket)
        setup = await control.request("commissioning.code", ttl_seconds=300)
        controller = controller_claim()
        start_body = {"contract_version": "1", **_start_payload(setup, controller)}
        app = create_app(LocalApiSettings(bootstrap=settings))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.168.1.30", 50000)),
            base_url="http://local.test",
        ) as client:
            endpoint = (await client.get("/api/local/v1/commissioning/endpoint")).json()

            unknown_suite = await client.post(
                "/api/local/v1/commissioning/pake/start",
                json={**start_body, "suite": "spake2plus-v1"},
            )
            assert unknown_suite.status_code == 422
            assert unknown_suite.json()["detail"]["code"] == "unsupported_suite"

            wrong = "00000012" if setup["setup_code"] != "00000012" else "00000013"
            start = (await client.post("/api/local/v1/commissioning/pake/start", json=start_body)).json()
            confirm, _ = phone_proof(start, code=wrong)
            refused = await client.post(
                "/api/local/v1/commissioning/pake/claim",
                json={"contract_version": "1", **confirm, "controller": controller},
            )
            assert refused.status_code == 401
            assert refused.json()["detail"]["code"] == "commissioning_denied"

            replayed = await client.post(
                "/api/local/v1/commissioning/pake/claim",
                json={"contract_version": "1", **confirm, "controller": controller},
            )
            assert replayed.status_code == 409
            assert replayed.json()["detail"]["code"] == "pake_handshake_unknown"

            start = (await client.post("/api/local/v1/commissioning/pake/start", json=start_body)).json()
            _pake_schema("start_result").validate(start)
            assert start["context"]["transport"] == "lan"
            assert start["context"]["tls_spki_fingerprint"] == endpoint["tls_spki_fingerprint"]
            confirm, expected_server_proof = phone_proof(start, code=setup["setup_code"])
            claimed = await client.post(
                "/api/local/v1/commissioning/pake/claim",
                json={"contract_version": "1", **confirm, "controller": controller},
            )
            assert claimed.status_code == 200, claimed.text
            _pake_schema("lan_claim_result").validate(claimed.json())
            assert claimed.json()["server_proof"] == expected_server_proof
            assert claimed.json()["state"]["claim_state"] == "claimed"
    finally:
        stop.set()
        await asyncio.wait_for(daemon, timeout=2)


@pytest.mark.asyncio
async def test_the_local_api_says_when_to_come_back(
    tmp_path: Path, short_runtime_dir: Path
) -> None:
    settings = BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=short_runtime_dir,
        control_socket=short_runtime_dir / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        setup_code_ttl_seconds=600,
    )
    stop = asyncio.Event()
    daemon = asyncio.create_task(run_daemon(settings, stop_event=stop))
    try:
        for _ in range(100):
            if settings.control_socket.exists():
                break
            await asyncio.sleep(0.01)
        control = BootstrapControlClient(settings.control_socket)
        setup = await control.request("commissioning.code", ttl_seconds=300)
        start_body = {"contract_version": "1", **_start_payload(setup, controller_claim())}
        app = create_app(LocalApiSettings(bootstrap=settings))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.168.1.31", 50000)),
            base_url="http://local.test",
        ) as client:
            for _ in range(LAN_SOURCE_PACE.capacity):
                ok = await client.post("/api/local/v1/commissioning/pake/start", json=start_body)
                assert ok.status_code == 200
            limited = await client.post("/api/local/v1/commissioning/pake/start", json=start_body)
            # Another address is its own line, until the shared LAN one is spent.
            neighbour = await client.post(
                "/api/local/v1/commissioning/pake/start",
                json=start_body,
                headers={"x-forwarded-for": "192.168.1.99"},
            )
    finally:
        stop.set()
        await asyncio.wait_for(daemon, timeout=2)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    retry_after_ms = limited.json()["detail"]["retry_after_ms"]
    assert 0 < retry_after_ms <= int(LAN_SOURCE_PACE.period * 1000)
    assert int(limited.headers["retry-after"]) == -(-retry_after_ms // 1000)
    # A forwarded-for header is the caller's claim, not its address: it is
    # paced with the socket it actually came from.
    assert neighbour.status_code == 429
