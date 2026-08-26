"""Lifecycle Workflow UDS authentication, protocol, and path-safety tests."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import socket
import stat
import struct
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from eidolon_sdk.device_foundation.v1.lifecycle import (
    ActorRef,
    OwnerAuthorizationContext,
)

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceRemovalRequest,
    DeviceRef,
    DeviceRemovalResult,
    RemovalCondition,
    WorkflowStep,
)
from eidolon_admin_server.lifecycle_workflow.daemon import (
    LifecycleWorkflowDaemon,
    _bind_socket,
    _unlink_owned_socket,
)
from eidolon_admin_server.lifecycle_workflow.peercred import (
    ExactUidWorkloadAuthorizer,
    LinuxSoPeerCredentialAdapter,
    UnixPeerCredential,
)
from eidolon_admin_server.lifecycle_workflow.protocol import (
    LifecycleRemovalCall,
    LifecycleWorkflowReply,
    RemovalOwnerAuthorizationContext,
    encode_frame,
    read_frame,
    removal_intent_id,
)
from eidolon_admin_server.lifecycle_workflow.settings import (
    LifecycleWorkflowSettings,
)

from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id

# Tests name the device they mean; the name becomes a real device
# instance id, which is a digest of a key and never a chosen string.
_DEVICE_1 = named_device_instance_id("device-1")


pytestmark = [pytest.mark.asyncio, pytest.mark.component]
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _request() -> ControllerDeviceRemovalRequest:
    return ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id="mobile-removal-1",
        owner_id="owner-1",
        controller_id="ectrl-0123456789abcdefabcd",
        device_id=_DEVICE_1,
    )


def _call() -> LifecycleRemovalCall:
    request = _request()
    device_ref = _result().device_ref
    return LifecycleRemovalCall(
        payload=request,
        authorization_context=RemovalOwnerAuthorizationContext(
            controller_grant_generation=7,
            reset_epoch=7,
            owner_authorization_context=OwnerAuthorizationContext(
                workload_principal_id="eidolon-lifecycle-workflow",
                actor=ActorRef(
                    principal_id=request.controller_id,
                    principal_type="controller",
                    owner_domain_id=request.owner_id,
                    granted_scopes=("device.read", "device.claim.revoke"),
                    authentication_strength="software",
                ),
                authorized_owner_domain_id=request.owner_id,
                scopes=("device.read", "device.claim.revoke"),
                intent_id=removal_intent_id(
                    ingress_request_id=request.request_id,
                    owner_domain_id=request.owner_id,
                ),
                target_device_ref=device_ref,
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
            ),
        ),
    )


def _result() -> DeviceRemovalResult:
    device_ref = DeviceRef(
        device_instance_id=_DEVICE_1,
        owner_domain_id="owner-1",
        owner_domain_generation=1,
        claim_generation=1,
        trust_epoch=1,
    )
    return DeviceRemovalResult(
        request_id="mobile-removal-1",
        intent_id="removal-intent-1",
        device_ref=device_ref,
        outcome="accepted",
        completed_stage="claim_revoked",
        recovery="retry-forward-same-request-id",
        steps=(WorkflowStep(name="hub_revocation", state="committed"),),
        conditions=(
            RemovalCondition(
                name="platform_access_revoked",
                state="true",
                authority="hub",
                observed_at=NOW,
            ),
            RemovalCondition(
                name="mount_removed",
                state="unknown",
                authority="kernel",
                observed_at=NOW,
            ),
            RemovalCondition(
                name="device_erase_acknowledged",
                state="unknown",
                authority="device-control",
                observed_at=NOW,
            ),
        ),
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def remove_controller_device(self, **kwargs) -> DeviceRemovalResult:
        self.calls.append(kwargs)
        return _result()

    async def close(self) -> None:
        self.closed = True


class _StaticPeerReader:
    def __init__(self, credential: UnixPeerCredential) -> None:
        self.credential = credential

    def read(self, _connection: socket.socket) -> UnixPeerCredential:
        return self.credential


class _FailingPeerReader:
    def read(self, _connection: socket.socket) -> UnixPeerCredential:
        raise OSError("peer credential unavailable")


class _ShortPeerCredentialSocket:
    def getsockopt(self, *_args) -> bytes:
        return b"\0" * (struct.calcsize("3i") - 1)


def _settings(socket_path: Path, *, allowed_uid: int) -> LifecycleWorkflowSettings:
    return LifecycleWorkflowSettings(
        socket_path=socket_path,
        removal_capability_socket=socket_path.parent / "broker.sock",
        state_dir=socket_path.parent,
        system_directory_url="http://127.0.0.1:8090",
        system_directory_uds=None,
        directory_timeout_seconds=1,
        authority_timeout_seconds=1,
        removal_observation_timeout_seconds=0,
        request_timeout_seconds=1,
        allowed_local_api_uid=allowed_uid,
    )


class _MemoryWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def get_extra_info(self, name: str):
        return object() if name == "socket" else None

    def write(self, value: bytes) -> None:
        self.buffer.extend(value)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


async def _handle(
    *,
    peer_reader,
    allowed_uid: int,
    request_bytes: bytes = b"",
) -> tuple[LifecycleWorkflowReply, _Service]:
    service = _Service()
    daemon = LifecycleWorkflowDaemon(
        settings=_settings(Path("/unused/workflow.sock"), allowed_uid=allowed_uid),
        service=service,  # type: ignore[arg-type]
        peer_reader=peer_reader,
    )
    reader = asyncio.StreamReader()
    reader.feed_data(request_bytes)
    reader.feed_eof()
    writer = _MemoryWriter()
    await daemon._handle_connection(reader, writer)  # type: ignore[arg-type]
    reply_reader = asyncio.StreamReader()
    reply_reader.feed_data(bytes(writer.buffer))
    reply_reader.feed_eof()
    reply = LifecycleWorkflowReply.model_validate(await read_frame(reply_reader))
    assert writer.closed is True
    return reply, service


async def test_allowed_uid_is_injected_as_workload_identity() -> None:
    uid = 41001
    call = _call()
    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, uid, 41000)),
        allowed_uid=uid,
        request_bytes=encode_frame(call),
    )

    assert reply.problem is None
    assert reply.result == _result()
    assert service.calls == [
        {
            "payload": _request(),
            "workload_principal_id": "eidolon-local-api",
            "authorization_context": call.authorization_context,
        }
    ]


async def test_wrong_uid_is_rejected_before_protocol_or_workflow() -> None:
    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, 41002, 41000)),
        allowed_uid=41001,
    )

    assert reply.problem is not None
    assert reply.problem.code == "AUTHN_INVALID"
    assert reply.problem.status_code == 401
    assert service.calls == []


async def test_peer_credential_reader_failure_is_fail_closed() -> None:
    reply, service = await _handle(
        peer_reader=_FailingPeerReader(),
        allowed_uid=41001,
    )

    assert reply.problem is not None
    assert reply.problem.code == "AUTHN_INVALID"
    assert service.calls == []


async def test_short_so_peercred_buffer_is_rejected(monkeypatch) -> None:
    import eidolon_admin_server.lifecycle_workflow.peercred as peercred

    monkeypatch.setattr(peercred.socket, "SO_PEERCRED", 17, raising=False)
    with pytest.raises(RuntimeError, match="invalid value"):
        LinuxSoPeerCredentialAdapter().read(  # type: ignore[arg-type]
            _ShortPeerCredentialSocket()
        )


@pytest.mark.parametrize("location", ["call", "payload"])
async def test_request_cannot_self_report_peer_uid(location: str) -> None:
    uid = 41001
    document = _call().model_dump(mode="json")
    if location == "call":
        document["peer_uid"] = uid
    else:
        document["payload"]["peer_uid"] = uid  # type: ignore[index]
    body = json.dumps(document, separators=(",", ":")).encode()
    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, uid, 41000)),
        allowed_uid=uid,
        request_bytes=struct.pack("!I", len(body)) + body,
    )

    assert reply.problem is not None
    assert reply.problem.code == "INVALID_REQUEST"
    assert service.calls == []


@pytest.mark.parametrize("drift", ["owner", "actor", "deadline"])
async def test_authorization_context_drift_is_denied_before_workflow(
    drift: str,
) -> None:
    uid = 41001
    document = _call().model_dump(mode="json")
    context = document["authorization_context"]["owner_authorization_context"]
    if drift == "owner":
        context["authorized_owner_domain_id"] = "owner-2"
        context["actor"]["owner_domain_id"] = "owner-2"
        context["target_device_ref"]["owner_domain_id"] = "owner-2"
    elif drift == "actor":
        context["actor"]["principal_id"] = "ectrl-fedcba9876543210abcd"
    else:
        context["issued_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        context["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    body = json.dumps(document, separators=(",", ":")).encode()

    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, uid, 41000)),
        allowed_uid=uid,
        request_bytes=struct.pack("!I", len(body)) + body,
    )

    assert reply.problem is not None
    assert reply.problem.code == "AUTHZ_DENIED"
    assert service.calls == []


async def test_removal_context_missing_revoke_scope_is_invalid_before_workflow() -> (
    None
):
    uid = 41001
    document = _call().model_dump(mode="json")
    document["authorization_context"]["owner_authorization_context"]["scopes"] = [
        "device.read"
    ]
    body = json.dumps(document, separators=(",", ":")).encode()

    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, uid, 41000)),
        allowed_uid=uid,
        request_bytes=struct.pack("!I", len(body)) + body,
    )

    assert reply.problem is not None
    assert reply.problem.code == "INVALID_REQUEST"
    assert service.calls == []


async def test_invalid_authorization_audience_fails_closed() -> None:
    uid = 41001
    document = _call().model_dump(mode="json")
    document["authorization_context"]["owner_authorization_context"]["audience"] = (
        "other-service"
    )
    body = json.dumps(document, separators=(",", ":")).encode()
    reply, service = await _handle(
        peer_reader=_StaticPeerReader(UnixPeerCredential(123, uid, 41000)),
        allowed_uid=uid,
        request_bytes=struct.pack("!I", len(body)) + body,
    )

    assert reply.problem is not None
    assert reply.problem.code == "INVALID_REQUEST"
    assert service.calls == []


async def test_exact_uid_authorizer_rejects_other_principals() -> None:
    policy = ExactUidWorkloadAuthorizer(expected_uid=41001)
    assert policy.authorize(UnixPeerCredential(1, 41001, 50)).principal_id == (
        "eidolon-local-api"
    )
    with pytest.raises(PermissionError, match="not authorized"):
        policy.authorize(UnixPeerCredential(1, 41002, 50))


def _runtime_root() -> tempfile.TemporaryDirectory[str]:
    temporary = tempfile.TemporaryDirectory(prefix="elw-path-", dir="/tmp")
    root = Path(temporary.name)
    os.chown(root, os.geteuid(), os.getegid())
    root.chmod(0o750)
    return temporary


@pytest.mark.parametrize("kind", ["regular", "symlink"])
async def test_socket_binder_refuses_regular_file_and_symlink(kind: str) -> None:
    temporary = _runtime_root()
    root = Path(temporary.name)
    path = root / "workflow.sock"
    target = root / "keep"
    target.write_text("do not replace", encoding="utf-8")
    if kind == "regular":
        path.write_text("do not replace", encoding="utf-8")
    else:
        path.symlink_to(target)
    try:
        with pytest.raises(RuntimeError, match="socket path is unsafe"):
            _bind_socket(path)
        assert path.exists() or path.is_symlink()
        assert target.read_text(encoding="utf-8") == "do not replace"
    finally:
        temporary.cleanup()


class _SocketDouble:
    def __init__(self, *, path: Path, connect_error: OSError | None = None) -> None:
        self.path = path
        self.connect_error = connect_error
        self.closed = False
        self.bound = False
        self.listening = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def connect(self, _path: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def setblocking(self, _blocking: bool) -> None:
        pass

    def bind(self, _path: str) -> None:
        self.path.write_text("socket-placeholder", encoding="utf-8")
        self.bound = True

    def listen(self, _backlog: int) -> None:
        self.listening = True

    def close(self) -> None:
        self.closed = True


async def test_socket_binder_refuses_a_live_listener(monkeypatch) -> None:
    import eidolon_admin_server.lifecycle_workflow.daemon as daemon

    temporary = _runtime_root()
    path = Path(temporary.name) / "workflow.sock"
    path.write_text("live-socket-placeholder", encoding="utf-8")
    probe = _SocketDouble(path=path)
    monkeypatch.setattr(daemon.stat, "S_ISSOCK", lambda _mode: True)
    monkeypatch.setattr(daemon.socket, "socket", lambda *_args: probe)
    try:
        with pytest.raises(RuntimeError, match="already active"):
            _bind_socket(path)
        assert path.read_text(encoding="utf-8") == "live-socket-placeholder"
        assert probe.closed is True
    finally:
        temporary.cleanup()


async def test_socket_binder_replaces_only_an_owned_stale_socket(monkeypatch) -> None:
    import eidolon_admin_server.lifecycle_workflow.daemon as daemon

    temporary = _runtime_root()
    path = Path(temporary.name) / "workflow.sock"
    path.write_text("stale-socket-placeholder", encoding="utf-8")
    probe = _SocketDouble(
        path=path,
        connect_error=OSError(errno.ECONNREFUSED, "stale socket"),
    )
    listener = _SocketDouble(path=path)
    sockets = iter((probe, listener))
    monkeypatch.setattr(daemon.stat, "S_ISSOCK", lambda _mode: True)
    monkeypatch.setattr(daemon.socket, "socket", lambda *_args: next(sockets))
    replacement = _bind_socket(path)
    try:
        assert replacement is listener
        assert listener.bound is True
        assert listener.listening is True
        assert stat.S_IMODE(path.stat().st_mode) == 0o660
        assert probe.closed is True
    finally:
        replacement.close()
        _unlink_owned_socket(path)
        temporary.cleanup()


async def test_old_daemon_does_not_unlink_a_replacement_socket() -> None:
    temporary = _runtime_root()
    path = Path(temporary.name) / "workflow.sock"
    service = _Service()
    daemon = LifecycleWorkflowDaemon(
        settings=_settings(path, allowed_uid=os.getuid()),
        service=service,  # type: ignore[arg-type]
        peer_reader=_StaticPeerReader(
            UnixPeerCredential(os.getpid(), os.getuid(), os.getgid())
        ),
    )
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            await daemon.start()
        except PermissionError:
            pytest.skip("test sandbox forbids binding Unix domain sockets")
        original_identity = (path.stat().st_dev, path.stat().st_ino)
        path.unlink()
        replacement.bind(str(path))
        replacement.listen()
        replacement_identity = (path.stat().st_dev, path.stat().st_ino)
        assert replacement_identity != original_identity

        await daemon.close()

        assert (path.stat().st_dev, path.stat().st_ino) == replacement_identity
    finally:
        replacement.close()
        _unlink_owned_socket(path)
        temporary.cleanup()
