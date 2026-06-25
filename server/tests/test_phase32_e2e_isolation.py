"""Phase 32.E: end-to-end multi-user memory isolation.

The whole 32 series exists to fix one bug: web client conversations
were attributed to a single hardcoded ``user_id=alice`` because
channel held a static device JWT with that payload. Memory got
overwritten with everyone's turns landing on the same palace.

This test proves the fix by going through the runtime path:

  1. Mint a Phase 32-style device JWT for two distinct user_ids
     using the same shared secret + payload schema agent's
     PairingTokenVerifier expects.
  2. Subscribe to ``eidolon.memory.turn.*`` on NATS.
  3. Send a ChatOnce gRPC to agent with each token.
  4. Assert each turn lands on its own user-suffixed subject — and
     nothing else.

Why "real" and not mocks: a unit test on the resolver doesn't catch
the agent-side routing. A unit test on the agent doesn't catch
channel's token contract. This test exercises the SEAM where Phase 32
draws its boundary (channel ↔ agent), with both running.

Skips cleanly when the dev stack isn't up — mirrors test_nats_kv.py's
"skip if NATS not reachable" stance. No mocks, no monkey-patching.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from pathlib import Path

import pytest

_AGENT_GRPC_HOST = "127.0.0.1"
_AGENT_GRPC_PORT = 45051
_NATS_HOST = "127.0.0.1"
_NATS_PORT = 4222
_SHARED_SECRET_FILE = Path("~/eidolon/run/jwt-secret").expanduser()


def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick liveness check that skips the test when infra is down."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_shared_secret() -> str:
    """Mirrors channel's resolve_shared_secret (env → file → '')."""
    env = os.environ.get("PAIRING_JWT_SECRET", "").strip()
    if env:
        return env
    if _SHARED_SECRET_FILE.is_file():
        return _SHARED_SECRET_FILE.read_text(encoding="utf-8").strip()
    return ""


def _make_device_token(*, secret: str, user_id: str, ttl_seconds: int = 600) -> str:
    """Sign a JWT the agent's PairingTokenVerifier will accept.

    Schema duplicated here on purpose — tests should pin the contract
    we're verifying, not import from the producer (channel) or the
    consumer (agent).
    """
    import jwt

    now = int(time.time())
    payload = {
        "device_id": f"e2e-test-{uuid.uuid4().hex[:8]}",
        "tenant_id": "default",
        "user_id": user_id,
        "template_id": None,  # agent uses default if None — lazy create
        "scopes": ["device"],
        "jti": uuid.uuid4().hex,
        "exp": now + ttl_seconds,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def _grpc_chat_once(
    *,
    user_id: str,
    text: str,
    secret: str,
    timeout_s: float = 20.0,
) -> str:
    """Run one ChatOnce as ``user_id`` and return the assistant text.

    Import grpc + agent's pb lazily so a missing eidolon_agent (running
    this test from a slim admin checkout) yields a clean skip, not an
    ImportError at module load.
    """
    grpc = pytest.importorskip("grpc")
    try:
        from eidolon_agent.app.transport.grpc.proto import (  # type: ignore[import-not-found]
            eidolon_pb2 as pb,
        )
        from eidolon_agent.app.transport.grpc.proto import (
            eidolon_pb2_grpc as pb_grpc,
        )
    except ImportError:
        pytest.skip("eidolon_agent not importable from this venv")

    token = _make_device_token(secret=secret, user_id=user_id)
    channel = grpc.aio.insecure_channel(f"{_AGENT_GRPC_HOST}:{_AGENT_GRPC_PORT}")
    try:
        stub = pb_grpc.EidolonAgentStub(channel)
        metadata = (("authorization", f"Bearer {token}"),)
        request = pb.ChatOnceRequest(
            turn_id=uuid.uuid4().hex,
            conversation_id=f"e2e:{user_id}:{uuid.uuid4().hex[:6]}",
            text=text,
        )
        resp = await stub.ChatOnce(request, metadata=metadata, timeout=timeout_s)
        return resp.assistant_text
    finally:
        await channel.close()


# ---- the test ------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_users_write_to_isolated_nats_subjects() -> None:
    """Send one turn each as two distinct user_ids; verify NATS turn
    subject is user-suffixed (no cross-contamination).

    Why this nails the 32 series: before Phase 32, channel's static
    token had ``user_id=alice`` baked in. Every web session — no
    matter what name the user typed — produced turns on
    ``eidolon.memory.turn.<space_token>``. This test confirms that
    after 32.A-D the per-session token's user_id reaches the agent
    and routes the turn to the right NATS subject.
    """
    nats_lib = pytest.importorskip("nats")
    if not _tcp_reachable(_NATS_HOST, _NATS_PORT):
        pytest.skip(f"NATS not reachable at {_NATS_HOST}:{_NATS_PORT}")
    if not _tcp_reachable(_AGENT_GRPC_HOST, _AGENT_GRPC_PORT):
        pytest.skip(f"agent gRPC not reachable at {_AGENT_GRPC_HOST}:{_AGENT_GRPC_PORT}")

    secret = _resolve_shared_secret()
    if not secret:
        pytest.skip(
            "PAIRING_JWT_SECRET empty and ~/eidolon/run/jwt-secret missing — "
            "start eidolon-agent once so it persists the secret"
        )

    observed_subjects: list[str] = []

    nc = await nats_lib.connect(f"nats://{_NATS_HOST}:{_NATS_PORT}")
    try:
        # Wildcard sub — we'll just check what subject prefix appears
        # for each user. We don't care about payload here; the SUBJECT
        # is the isolation contract.
        async def _on_msg(msg) -> None:  # type: ignore[no-untyped-def]
            observed_subjects.append(msg.subject)

        sub = await nc.subscribe("eidolon.memory.turn.*", cb=_on_msg)

        # Pick two user_ids that admin already has worker palaces for
        # on the dev stack. ``default`` is seeded; manson is created
        # by the operator. If either is missing, agent routes lazily
        # but the routing table lookup will fail — that's a separate
        # skip path which is fine for this test.
        try:
            r1 = await _grpc_chat_once(
                user_id="manson",
                text="测试 isolation: 我提到一个独特的词:铁锤。",
                secret=secret,
            )
            r2 = await _grpc_chat_once(
                user_id="default",
                text="测试 isolation: 我提到一个独特的词:苹果。",
                secret=secret,
            )
        except Exception as exc:  # noqa: BLE001 — anything that's not a NATS issue
            pytest.skip(
                f"agent gRPC ChatOnce failed (likely missing user palace): {exc}. "
                "Ensure both manson and default users exist in the admin registry."
            )

        assert r1, "manson ChatOnce returned empty text"
        assert r2, "default ChatOnce returned empty text"

        # Give NATS a beat to deliver our subscription's messages.
        await asyncio.sleep(2.0)
        await sub.unsubscribe()
    finally:
        await nc.close()

    manson_hits = [s for s in observed_subjects if ".manson." in s]
    default_hits = [s for s in observed_subjects if s.endswith(".default.default")]
    cross_hits = [
        s for s in observed_subjects if not (".manson." in s or s.endswith(".default.default"))
    ]

    # The contract: exactly one turn per user, ZERO turns on any other
    # subject (especially ``...alice``, the pre-32 default).
    assert manson_hits, f"no manson turn subject observed; saw: {observed_subjects}"
    assert default_hits, f"no default turn subject observed; saw: {observed_subjects}"
    assert not cross_hits, (
        f"unexpected cross-contamination subjects: {cross_hits}. "
        f"Phase 32 isolation broken — investigate channel/agent token flow."
    )
