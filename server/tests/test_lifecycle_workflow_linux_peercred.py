"""Linux-only proof that Lifecycle Workflow consumes kernel peer identity."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
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
    LinuxSoPeerCredentialAdapter,
)
from eidolon_admin_server.lifecycle_workflow.protocol import (
    LifecycleRemovalCall,
    LifecycleWorkflowReply,
    RemovalOwnerAuthorizationContext,
    encode_frame,
    read_frame,
    removal_intent_id,
)
from eidolon_admin_server.lifecycle_workflow.settings import LifecycleWorkflowSettings


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="requires Linux kernel SO_PEERCRED; a Darwin skip is not HIL evidence",
    ),
]
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _result() -> DeviceRemovalResult:
    ref = DeviceRef(
        device_instance_id="device-linux-peer",
        owner_domain_id="owner-linux-peer",
        owner_domain_generation=1,
        claim_generation=1,
        trust_epoch=1,
    )
    return DeviceRemovalResult(
        request_id="linux-peer-removal-1",
        intent_id="linux-peer-intent-1",
        device_ref=ref,
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
                name="channel_access_revoked",
                state="unknown",
                authority="device-control",
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


def _call() -> LifecycleRemovalCall:
    payload = ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id="linux-peer-removal-1",
        owner_id="owner-linux-peer",
        controller_id="ectrl-0123456789abcdefabcd",
        device_id="device-linux-peer",
    )
    return LifecycleRemovalCall(
        payload=payload,
        authorization_context=RemovalOwnerAuthorizationContext(
            controller_grant_generation=3,
            reset_epoch=3,
            owner_authorization_context=OwnerAuthorizationContext(
                workload_principal_id="eidolon-lifecycle-workflow",
                actor=ActorRef(
                    principal_id=payload.controller_id,
                    principal_type="controller",
                    owner_domain_id=payload.owner_id,
                    granted_scopes=("device.read", "device.claim.revoke"),
                    authentication_strength="software",
                ),
                authorized_owner_domain_id=payload.owner_id,
                scopes=("device.read", "device.claim.revoke"),
                intent_id=removal_intent_id(
                    ingress_request_id=payload.request_id,
                    owner_domain_id=payload.owner_id,
                ),
                target_device_ref=_result().device_ref,
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
            ),
        ),
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def remove_controller_device(self, **kwargs) -> DeviceRemovalResult:
        self.calls.append(kwargs)
        return _result()

    async def close(self) -> None:
        pass


def _settings(path: Path, allowed_uid: int) -> LifecycleWorkflowSettings:
    return LifecycleWorkflowSettings(
        socket_path=path,
        removal_capability_socket=path.parent / "broker.sock",
        state_dir=path.parent,
        system_directory_url="http://127.0.0.1:8090",
        system_directory_uds=None,
        directory_timeout_seconds=1,
        authority_timeout_seconds=1,
        removal_observation_timeout_seconds=0,
        request_timeout_seconds=1,
        allowed_local_api_uid=allowed_uid,
    )


async def _exchange(path: Path, *, send_call: bool) -> LifecycleWorkflowReply:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        if send_call:
            writer.write(encode_frame(_call()))
            await writer.drain()
        return LifecycleWorkflowReply.model_validate(await read_frame(reader))
    finally:
        writer.close()
        await writer.wait_closed()


async def _exchange_from_uid(
    path: Path, *, uid: int, shared_gid: int
) -> LifecycleWorkflowReply:
    """Run the client in a real child process after dropping to numeric IDs."""

    read_descriptor, write_descriptor = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_descriptor)
            os.setgroups([shared_gid])
            os.setgid(shared_gid)
            os.setuid(uid)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(path))
            client.sendall(encode_frame(_call()))
            response = bytearray()
            while chunk := client.recv(65536):
                response.extend(chunk)
            client.close()
            os.write(write_descriptor, bytes(response))
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_descriptor)
    def read_response() -> bytes:
        chunks: list[bytes] = []
        while chunk := os.read(read_descriptor, 65536):
            chunks.append(chunk)
        return b"".join(chunks)

    response = await asyncio.to_thread(read_response)
    os.close(read_descriptor)
    _pid, status = await asyncio.to_thread(os.waitpid, pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    reader = asyncio.StreamReader()
    reader.feed_data(response)
    reader.feed_eof()
    return LifecycleWorkflowReply.model_validate(await read_frame(reader))


async def test_real_af_unix_so_peercred_reports_the_kernel_process_identity() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        credential = LinuxSoPeerCredentialAdapter().read(left)
    finally:
        left.close()
        right.close()

    assert credential.pid == os.getpid()
    assert credential.uid == os.getuid()
    assert credential.gid == os.getgid()


@pytest.mark.parametrize("allowed", [True, False])
async def test_real_so_peercred_controls_the_workflow_before_request_parsing(
    allowed: bool,
) -> None:
    temporary = tempfile.TemporaryDirectory(prefix="elw-linux-", dir="/tmp")
    root = Path(temporary.name)
    os.chown(root, os.geteuid(), os.getegid())
    root.chmod(0o750)
    path = root / "workflow.sock"
    service = _Service()
    expected_uid = os.getuid() if allowed else os.getuid() + 1
    daemon = LifecycleWorkflowDaemon(
        settings=_settings(path, expected_uid),
        service=service,  # type: ignore[arg-type]
    )
    await daemon.start()
    try:
        reply = await _exchange(path, send_call=allowed)
    finally:
        await daemon.close()
        temporary.cleanup()

    if allowed:
        assert reply.problem is None
        assert reply.result == _result()
        assert service.calls[0]["workload_principal_id"] == "eidolon-local-api"
    else:
        assert reply.problem is not None
        assert reply.problem.code == "AUTHN_INVALID"
        assert service.calls == []


@pytest.mark.skipif(
    os.geteuid() != 0,
    reason="requires root to create real distinct numeric process principals",
)
async def test_real_distinct_uids_share_socket_acl_but_only_local_uid_is_authorized() -> None:
    local_uid = 61001
    attacker_uid = 61002
    socket_gid = 61003
    temporary = tempfile.TemporaryDirectory(prefix="elw-linux-users-", dir="/tmp")
    root = Path(temporary.name)
    root.chmod(0o750)
    path = root / "workflow.sock"
    service = _Service()
    daemon = LifecycleWorkflowDaemon(
        settings=_settings(path, local_uid),
        service=service,  # type: ignore[arg-type]
    )
    await daemon.start()
    os.chown(root, 0, socket_gid)
    root.chmod(0o2750)
    os.chown(path, 0, socket_gid)
    path.chmod(0o660)
    try:
        accepted = await _exchange_from_uid(
            path, uid=local_uid, shared_gid=socket_gid
        )
        denied = await _exchange_from_uid(
            path, uid=attacker_uid, shared_gid=socket_gid
        )
    finally:
        await daemon.close()
        temporary.cleanup()

    assert accepted.problem is None
    assert denied.problem is not None
    assert denied.problem.code == "AUTHN_INVALID"
    assert len(service.calls) == 1
    assert service.calls[0]["workload_principal_id"] == "eidolon-local-api"


async def test_real_linux_socket_path_refuses_live_and_replaces_stale() -> None:
    temporary = tempfile.TemporaryDirectory(prefix="elw-linux-path-", dir="/tmp")
    root = Path(temporary.name)
    os.chown(root, os.geteuid(), os.getegid())
    root.chmod(0o750)
    path = root / "workflow.sock"
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    live.bind(str(path))
    live.listen()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            _bind_socket(path)
    finally:
        live.close()

    stale_inode = path.stat().st_ino
    replacement = _bind_socket(path)
    try:
        assert path.stat().st_ino != stale_inode
        assert (path.stat().st_mode & 0o777) == 0o660
    finally:
        replacement.close()
        _unlink_owned_socket(path)
        temporary.cleanup()
