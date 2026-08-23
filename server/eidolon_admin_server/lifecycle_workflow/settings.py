"""Fail-closed settings for the isolated Lifecycle Workflow."""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class LifecycleWorkflowSettings:
    socket_path: Path
    removal_capability_socket: Path
    state_dir: Path
    system_directory_url: str
    system_directory_uds: Path | None
    directory_timeout_seconds: float
    authority_timeout_seconds: float
    removal_observation_timeout_seconds: float
    request_timeout_seconds: float
    allowed_local_api_uid: int


def load_lifecycle_workflow_settings(
    environ: Mapping[str, str] | None = None,
) -> LifecycleWorkflowSettings:
    env = os.environ if environ is None else environ
    socket_path = Path(
        env.get(
            "EIDOLON_LIFECYCLE_WORKFLOW_SOCKET",
            "/run/eidolon-lifecycle/workflow.sock",
        )
    )
    state_dir = Path(
        env.get("EIDOLON_LIFECYCLE_STATE_DIR", "/var/lib/eidolon-lifecycle")
    )
    removal_capability_socket = Path(
        env.get(
            "EIDOLON_LIFECYCLE_REMOVAL_CAPABILITY_SOCKET",
            "/run/eidolon-removal-capability/broker.sock",
        )
    )
    if (
        not socket_path.is_absolute()
        or not state_dir.is_absolute()
        or not removal_capability_socket.is_absolute()
    ):
        raise ValueError("Lifecycle Workflow paths must be absolute")
    account = env.get(
        "EIDOLON_LIFECYCLE_ALLOWED_LOCAL_API_USER", "eidolon-local-api"
    ).strip()
    if not account:
        raise ValueError("Lifecycle Workflow Local API account is required")
    try:
        allowed_uid = pwd.getpwnam(account).pw_uid
    except KeyError as exc:
        raise ValueError("Lifecycle Workflow Local API account does not exist") from exc
    try:
        directory_timeout = float(
            env.get("EIDOLON_LIFECYCLE_DIRECTORY_TIMEOUT_SECONDS", "2")
        )
        authority_timeout = float(
            env.get("EIDOLON_LIFECYCLE_AUTHORITY_TIMEOUT_SECONDS", "3")
        )
        observation_timeout = float(
            env.get("EIDOLON_LIFECYCLE_REMOVAL_OBSERVATION_TIMEOUT_SECONDS", "2")
        )
        request_timeout = float(
            env.get("EIDOLON_LIFECYCLE_REQUEST_TIMEOUT_SECONDS", "5")
        )
    except ValueError as exc:
        raise ValueError("Lifecycle Workflow timeout setting is invalid") from exc
    if not 0 < directory_timeout <= 30 or not 0 < authority_timeout <= 30:
        raise ValueError("Lifecycle Workflow authority timeout is invalid")
    if (
        not 0 <= observation_timeout <= 10
        or not 0 < request_timeout <= 30
    ):
        raise ValueError("Lifecycle Workflow timing policy is invalid")
    directory_uds_raw = env.get(
        "EIDOLON_LIFECYCLE_SYSTEM_DIRECTORY_UDS", "/run/eidolon/system.sock"
    ).strip()
    directory_uds = Path(directory_uds_raw) if directory_uds_raw else None
    if directory_uds is not None and not directory_uds.is_absolute():
        raise ValueError("Lifecycle Workflow directory socket must be absolute")
    return LifecycleWorkflowSettings(
        socket_path=socket_path,
        removal_capability_socket=removal_capability_socket,
        state_dir=state_dir,
        system_directory_url=env.get(
            "EIDOLON_LIFECYCLE_SYSTEM_DIRECTORY_URL", "http://127.0.0.1:8090"
        ),
        system_directory_uds=directory_uds,
        directory_timeout_seconds=directory_timeout,
        authority_timeout_seconds=authority_timeout,
        removal_observation_timeout_seconds=observation_timeout,
        request_timeout_seconds=request_timeout,
        allowed_local_api_uid=allowed_uid,
    )
