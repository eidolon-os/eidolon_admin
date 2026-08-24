"""Dedicated, peer-authenticated Lifecycle Workflow daemon."""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import stat
from contextlib import suppress
from pathlib import Path

import httpx
from pydantic import ValidationError

from ..app.control_plane.clients import KernelMountClient
from ..app.control_plane.directory import SystemDirectoryClient
from ..app.control_plane.errors import AuthorityFailure
from ..app.control_plane.removal_intents import SqliteRemovalIntentStore
from ..app.control_plane.service import ControlPlaneService
from .peercred import ExactUidWorkloadAuthorizer, LinuxSoPeerCredentialAdapter
from .protocol import (
    LifecycleRemovalCall,
    LifecycleWorkflowProblem,
    LifecycleWorkflowReply,
    read_frame,
    removal_intent_id,
    write_frame,
)
from .settings import LifecycleWorkflowSettings, load_lifecycle_workflow_settings
from ..systemd_notify import SystemdNotifier


class LifecycleWorkflowDaemon:
    def __init__(
        self,
        *,
        settings: LifecycleWorkflowSettings,
        service: ControlPlaneService,
        peer_reader=None,
        peer_authorizer=None,
    ) -> None:
        self._settings = settings
        self._service = service
        self._peer_reader = peer_reader or LinuxSoPeerCredentialAdapter()
        self._peer_authorizer = peer_authorizer or ExactUidWorkloadAuthorizer(
            expected_uid=settings.allowed_local_api_uid
        )
        self._server: asyncio.AbstractServer | None = None
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None

    async def start(self) -> None:
        ready = getattr(getattr(self._service, "hub", None), "ready", None)
        if ready is not None:
            await ready()
        listener = _bind_socket(self._settings.socket_path)
        socket_state = self._settings.socket_path.stat()
        socket_identity = (socket_state.st_dev, socket_state.st_ino)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                sock=listener,
                start_serving=True,
            )
        except Exception:
            listener.close()
            _unlink_owned_socket(
                self._settings.socket_path, expected_identity=socket_identity
            )
            raise
        self._listener = listener
        self._socket_identity = socket_identity
        SystemdNotifier.from_environ().ready("Lifecycle Workflow socket ready")

    async def close(self) -> None:
        SystemdNotifier.from_environ().stopping("Lifecycle Workflow stopping")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._listener is not None:
            self._listener.close()
        _unlink_owned_socket(
            self._settings.socket_path, expected_identity=self._socket_identity
        )
        await self._service.close()

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            connection = writer.get_extra_info("socket")
            if connection is None:
                raise RuntimeError("accepted Unix socket is unavailable")
            credential = self._peer_reader.read(connection)
            workload = self._peer_authorizer.authorize(credential)
        except (OSError, RuntimeError, PermissionError):
            await _reply_problem(
                writer,
                code="AUTHN_INVALID",
                detail="Local workload authentication failed",
                status_code=401,
            )
            await _close_writer(writer)
            return
        try:
            document = await asyncio.wait_for(
                read_frame(reader), timeout=self._settings.request_timeout_seconds
            )
            call = LifecycleRemovalCall.model_validate(document)
            context = call.authorization_context
            if context.deadline <= _utc_now():
                raise PermissionError("Controller delegation expired")
            # Workload identity is injected from SO_PEERCRED. No request field can
            # replace it; the domain Controller remains a separate audit actor.
            if workload.principal_id != "eidolon-local-api":
                raise PermissionError("Local workload principal is not authorized")
            if (
                context.issuer_workload_principal_id != workload.principal_id
                or context.presenter_workload_principal_id
                != "eidolon-lifecycle-workflow"
                or context.owner_authorization_context.workload_principal_id
                != context.presenter_workload_principal_id
                or context.owner_authorization_context.audience
                != "eidolon-admission"
                or set(context.owner_authorization_context.scopes)
                != {"device.read", "device.claim.revoke"}
                or context.actor_controller_id != call.payload.controller_id
                or context.owner_authorization_context.actor.principal_type
                != "controller"
                or context.target_device_ref.device_instance_id
                != call.payload.device_id
                or context.intent_id
                != removal_intent_id(
                    ingress_request_id=call.payload.request_id,
                    owner_domain_id=str(context.authorized_owner_domain_id),
                )
                or context.controller_grant_generation != context.reset_epoch
            ):
                raise PermissionError("Removal delegation does not match its request")
            result = await self._service.remove_controller_device(
                payload=call.payload,
                workload_principal_id=workload.principal_id,
                authorization_context=context,
            )
            await write_frame(writer, LifecycleWorkflowReply(result=result))
        except PermissionError:
            await _reply_problem(
                writer,
                code="AUTHZ_DENIED",
                detail="Removal delegation is not authorized",
                status_code=403,
            )
        except (
            ValidationError,
            ValueError,
            asyncio.IncompleteReadError,
            TimeoutError,
        ):
            await _reply_problem(
                writer,
                code="INVALID_REQUEST",
                detail="Lifecycle Workflow request is invalid",
                status_code=422,
            )
        except AuthorityFailure as exc:
            await _reply_problem(
                writer,
                code="WORKFLOW_FAILURE",
                detail=str(exc),
                status_code=exc.status_code,
            )
        except Exception:
            await _reply_problem(
                writer,
                code="WORKFLOW_UNAVAILABLE",
                detail="Lifecycle Workflow is unavailable",
                status_code=503,
            )
        finally:
            await _close_writer(writer)


def _utc_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def _reply_problem(writer, *, code, detail, status_code) -> None:
    with suppress(Exception):
        await write_frame(
            writer,
            LifecycleWorkflowReply(
                problem=LifecycleWorkflowProblem(
                    code=code,
                    detail=detail,
                    status_code=status_code,
                )
            ),
        )


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with suppress(BrokenPipeError, ConnectionError, OSError):
        await writer.wait_closed()


def _bind_socket(path: Path) -> socket.socket:
    parent = path.parent
    parent_state = parent.stat()
    if (
        not stat.S_ISDIR(parent_state.st_mode)
        or parent_state.st_uid != os.geteuid()
        or parent_state.st_gid not in {os.getegid(), *os.getgroups()}
        or stat.S_IMODE(parent_state.st_mode) not in {0o750, 0o2750}
    ):
        raise RuntimeError("Lifecycle Workflow runtime directory ownership drifted")
    if path.exists() or path.is_symlink():
        state = path.lstat()
        if not stat.S_ISSOCK(state.st_mode) or state.st_uid != os.geteuid():
            raise RuntimeError("Lifecycle Workflow socket path is unsafe")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(path))
        except OSError as exc:
            if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                raise RuntimeError("Lifecycle Workflow socket cannot be replaced") from exc
        else:
            raise RuntimeError("Lifecycle Workflow socket is already active")
        finally:
            probe.close()
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.setblocking(False)
        listener.bind(str(path))
        os.chmod(path, 0o660, follow_symlinks=False)
        listener.listen(socket.SOMAXCONN)
    except Exception:
        listener.close()
        _unlink_owned_socket(path)
        raise
    return listener


def _unlink_owned_socket(
    path: Path, *, expected_identity: tuple[int, int] | None = None
) -> None:
    with suppress(FileNotFoundError):
        state = path.lstat()
        identity = (state.st_dev, state.st_ino)
        if (
            stat.S_ISSOCK(state.st_mode)
            and state.st_uid == os.geteuid()
            and (expected_identity is None or identity == expected_identity)
        ):
            path.unlink()


def _build_service(settings: LifecycleWorkflowSettings) -> ControlPlaneService:
    from .capability import BrokeredRemovalHubClient, BrokerMarkerIssuer

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.authority_timeout_seconds), trust_env=False
    )
    directory = SystemDirectoryClient(
        base_url=settings.system_directory_url,
        uds_path=settings.system_directory_uds,
        timeout_seconds=settings.directory_timeout_seconds,
        client=None if settings.system_directory_uds else http_client,
    )
    service = ControlPlaneService(
        directory=directory,
        data=object(),  # not reachable from the removal-only daemon
        workspace=object(),  # not reachable from the removal-only daemon
        hub=BrokeredRemovalHubClient(
            socket_path=settings.removal_capability_socket,
            timeout_seconds=settings.authority_timeout_seconds,
        ),
        kernel=KernelMountClient(
            directory=directory,
            client=http_client,
            timeout_seconds=settings.authority_timeout_seconds,
        ),
        memory=object(),  # not reachable from the removal-only daemon
        hub_credentials=BrokerMarkerIssuer(),
        removal_intents=SqliteRemovalIntentStore(
            settings.state_dir / "lifecycle-workflows.sqlite3"
        ),
        removal_observation_timeout_seconds=(
            settings.removal_observation_timeout_seconds
        ),
    )
    original_close = service.close

    async def close() -> None:
        await original_close()
        await http_client.aclose()

    service.close = close  # type: ignore[method-assign]
    return service


async def _run(settings: LifecycleWorkflowSettings) -> None:
    daemon = LifecycleWorkflowDaemon(
        settings=settings,
        service=_build_service(settings),
    )
    try:
        await daemon.start()
        await daemon.serve_forever()
    finally:
        await daemon.close()


def main() -> None:
    asyncio.run(_run(load_lifecycle_workflow_settings()))


if __name__ == "__main__":
    main()
