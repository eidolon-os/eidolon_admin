"""What the Host says when it refuses a phone, and whether it says it out loud.

A Host with `claim_state=unclaimed` and zero Controller grants refused a phone,
and the phone reported "it may already be claimed" — the opposite of the truth.
The refusal itself was correct; two things around it were not. The Host answered
`controller_denied` for both "nobody is authorized here yet" and "this Host
belongs to someone else", and it logged neither, so the only way to learn which
had happened was to open bootstrap.sqlite3 by hand.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator, FormatChecker

from eidolon_admin_server.bootstrap.adapters.network import InMemoryNetworkProvisioning
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import CommissioningService
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.domain import NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.service import BootstrapService

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts/bootstrap/v1"


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def _service(tmp_path: Path) -> tuple[BootstrapService, InMemoryBootstrapStateStore]:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    service.initialize()
    return service, store


def _session(store: InMemoryBootstrapStateStore) -> CommissioningProtocolSession:
    return CommissioningProtocolSession(
        CommissioningService(store=store, network=InMemoryNetworkProvisioning())
    )


async def _refuse(session: CommissioningProtocolSession, request: dict) -> dict:
    answer = await session.handle(request)
    assert answer["ok"] is False, answer
    return answer["error"]


def _controller_public_key() -> str:
    public_der = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.urlsafe_b64encode(public_der).rstrip(b"=").decode("ascii")


def _challenge(controller_id: str = "ectrl-" + "c" * 20) -> dict:
    return {
        "contract_version": "1",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "operation": "controller.challenge",
        "payload": {"controller_id": controller_id},
    }


# --- the endpoint document against its own contract ------------------------


def test_the_endpoint_document_satisfies_its_published_schema(tmp_path: Path) -> None:
    """The check that was missing while the schema drifted away from the Host.

    `commissioning-endpoint.schema.json` required a field named
    `development_setup` long after the Host had renamed it `setup_session` and
    added `local_api_base_urls`; with `additionalProperties: false` it would
    have rejected the very document it describes. Nothing referenced it, so
    nothing said so — and the phone's reading of that document was aligned by
    hand instead, which is how the wording could drift too.
    """

    schema = json.loads(
        (CONTRACTS / "commissioning-endpoint.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    service, _ = _service(tmp_path)

    # Both shapes of the one field a Controller branches on.
    validator.validate(service.commissioning_endpoint())
    service.issue_setup_code(300)
    opened = service.commissioning_endpoint()
    assert opened["setup_session"] is not None
    validator.validate(opened)


def test_every_refusal_code_the_host_raises_is_in_the_error_contract() -> None:
    """A code the Host can send that the contract does not list is undocumented."""

    schema = json.loads((CONTRACTS / "error.schema.json").read_text(encoding="utf-8"))
    declared = set(schema["properties"]["code"]["enum"])
    source = Path(__file__).resolve().parents[1] / "eidolon_admin_server/bootstrap"
    raised = {
        match.group(1)
        for path in source.rglob("*.py")
        for match in re.finditer(
            r'CommissioningRequestRejected\(\s*"([a-z_]+)"',
            path.read_text(encoding="utf-8"),
        )
    }

    assert raised, "found no refusal sites to check"
    assert "already_claimed" in raised, "already_claimed is declared but never raised"
    assert raised <= declared, sorted(raised - declared)


# --- which refusal, and why it matters -------------------------------------


@pytest.mark.asyncio
async def test_an_unclaimed_host_with_no_grants_says_nobody_is_authorized(
    tmp_path: Path,
) -> None:
    _, store = _service(tmp_path)

    error = await _refuse(_session(store), _challenge())

    # Not `already_claimed`: this Host has no owner to belong to. The phone must
    # be able to say "this Host is waiting for its first Setup code" from this.
    assert error["code"] == "controller_denied"


@pytest.mark.asyncio
async def test_a_claimed_host_says_it_is_claimed_rather_than_just_denying(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    # A claim requires a connected network, as it does on a real Host.
    service.reconcile_network_state(NetworkState.CONNECTED)
    credential = service.issue_setup_code(300)
    session = _session(store)
    assert (
        await session.handle(
            {
                "contract_version": "1",
                "request_id": "22222222-2222-4222-8222-222222222222",
                "operation": "session.authenticate",
                "payload": {
                    "commissioning_id": credential["commissioning_id"],
                    "setup_code": credential["setup_code"],
                },
            }
        )
    )["ok"] is True
    claimed = await session.handle(
        {
            "contract_version": "1",
            "request_id": "33333333-3333-4333-8333-333333333333",
            "operation": "claim.complete",
            "payload": {
                "public_key": _controller_public_key(),
                "display_name": "Pad",
                "platform": "android",
            },
        }
    )
    assert claimed["ok"] is True, claimed

    # A different phone now asks. The window is closed either way, so
    # `setup_session` is null for both this and the test above; only the
    # refusal code tells them apart.
    error = await _refuse(_session(store), _challenge("ectrl-" + "b" * 20))

    assert error["code"] == "already_claimed"
    assert error["retryable"] is False


@pytest.mark.asyncio
async def test_a_refusal_is_written_to_the_log_with_its_operation_and_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _, store = _service(tmp_path)

    with caplog.at_level(logging.WARNING, logger="eidolon.bootstrap.commissioning"):
        await _refuse(_session(store), _challenge())

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "controller.challenge" in message
    assert "controller_denied" in message


@pytest.mark.asyncio
async def test_a_refusal_before_the_operation_is_read_still_names_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`operation` is bound before the try, or this log raises NameError."""

    _, store = _service(tmp_path)
    request = {**_challenge(), "contract_version": "99"}

    with caplog.at_level(logging.WARNING, logger="eidolon.bootstrap.commissioning"):
        error = await _refuse(_session(store), request)

    assert error["code"] == "unsupported_contract"
    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "controller.challenge" in message


@pytest.mark.asyncio
async def test_a_refusal_log_never_carries_the_payload(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Setup codes, controller keys and Wi-Fi passphrases all arrive in payload."""

    service, store = _service(tmp_path)
    credential = service.issue_setup_code(300)
    secret = credential["setup_code"]
    with caplog.at_level(logging.WARNING, logger="eidolon.bootstrap.commissioning"):
        await _refuse(
            _session(store),
            {
                "contract_version": "1",
                "request_id": "44444444-4444-4444-8444-444444444444",
                "operation": "session.authenticate",
                "payload": {
                    "commissioning_id": credential["commissioning_id"],
                    "setup_code": "0" * len(secret),
                },
            },
        )

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "session.authenticate" in message
    assert "commissioning_denied" in message
    assert secret not in message
    assert "0" * len(secret) not in message
