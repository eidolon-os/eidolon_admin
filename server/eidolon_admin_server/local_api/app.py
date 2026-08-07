"""Minimal local product API.

Public Host routes, Host proof, and Controller-authenticated short-lived
sessions exist. Workspace Setup is the first product mutation: it is
Controller-authenticated, Host-idempotent, and reaches Data only through
Admin's exact loopback contract.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..bootstrap.control import BootstrapControlClient, BootstrapControlError
from .auth import LocalControllerSessionStore
from .config import LocalApiSettings, load_local_api_settings
from .workspace import (
    AdminWorkspaceClient,
    AdminWorkspacePort,
    WorkspaceSetupError,
    WorkspaceSetupRequest,
    host_workspace_operation_id,
    workspace_status,
)


class HostProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class ControllerChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")


class ControllerProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    purpose: Literal["eidolon-controller-local-auth-v1"]
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    reset_epoch: int = Field(ge=0)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{8,256}$")


def create_app(
    settings: LocalApiSettings | None = None,
    *,
    workspace_client: AdminWorkspacePort | None = None,
) -> FastAPI:
    resolved = settings or load_local_api_settings()
    workspace = workspace_client or AdminWorkspaceClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_workspace_client = workspace_client is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if owns_workspace_client:
                await workspace.close()

    app = FastAPI(
        title="Eidolon Local API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    client = BootstrapControlClient(resolved.bootstrap.control_socket)
    sessions = LocalControllerSessionStore(
        ttl_seconds=resolved.session_ttl_seconds,
    )

    async def request_bootstrap(
        operation: str,
        *,
        authentication: bool = False,
        **parameters: Any,
    ) -> dict:
        try:
            return await client.request(operation, **parameters)
        except BootstrapControlError as exc:
            if authentication and exc.code == "ControllerAuthenticationRejected":
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Controller authentication failed",
                ) from exc
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap control plane unavailable",
            ) from exc
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap control plane unavailable",
            ) from exc

    @app.get("/healthz")
    async def health() -> dict:
        result = await request_bootstrap("health")
        return {"status": "ok", "bootstrap": result["status"]}

    @app.get("/api/local/v1/descriptor")
    async def descriptor() -> dict:
        return await request_bootstrap("descriptor")

    @app.get("/api/local/v1/host")
    async def host_overview() -> dict:
        result = await request_bootstrap("health")
        return {
            "contract_version": "1",
            "status": result["status"],
            "mode": result["mode"],
            "descriptor": result["descriptor"],
            "state": result["state"],
        }

    @app.post("/api/local/v1/host/proof")
    async def host_proof(request: HostProofRequest) -> dict:
        return await request_bootstrap("host.prove", challenge=request.challenge)

    @app.post("/api/local/v1/auth/challenges")
    async def controller_challenge(request: ControllerChallengeRequest) -> dict:
        return await request_bootstrap(
            "controller.challenge",
            authentication=True,
            controller_id=request.controller_id,
        )

    @app.post("/api/local/v1/auth/sessions")
    async def controller_session(request: ControllerProofRequest) -> dict:
        principal = await request_bootstrap(
            "controller.authenticate",
            authentication=True,
            proof=request.model_dump(),
        )
        token, session = sessions.issue(principal)
        return {
            "contract_version": "1",
            "token_type": "Bearer",
            "access_token": token,
            "expires_at": session.expires_at.isoformat().replace("+00:00", "Z"),
            "controller": principal,
        }

    @app.get("/api/local/v1/auth/session")
    async def current_controller_session(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, session = await authenticated_controller(authorization)
        return {
            "contract_version": "1",
            "expires_at": session.expires_at.isoformat().replace("+00:00", "Z"),
            "controller": principal,
        }

    async def authenticated_controller(
        authorization: str | None,
    ) -> tuple[dict, Any]:
        token = _bearer_token(authorization)
        session = sessions.get(token)
        if session is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Controller session is missing or expired",
            )
        try:
            principal = await request_bootstrap(
                "controller.validate",
                authentication=True,
                controller_id=session.controller_id,
                reset_epoch=session.reset_epoch,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                sessions.revoke(session)
            raise
        return principal, session

    async def operation_id_for_host() -> str:
        host = await request_bootstrap("descriptor")
        try:
            return host_workspace_operation_id(host["host_id"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap Host identity is unavailable",
            ) from exc

    @app.get("/api/local/v1/setup/workspace")
    async def get_workspace(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, _session = await authenticated_controller(authorization)
        operation_id = await operation_id_for_host()
        owner_id = principal.get("owner_id")
        if owner_id is None:
            return workspace_status(operation_id=operation_id, result=None)
        try:
            result = await workspace.get(operation_id)
        except WorkspaceSetupError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        if result.owner.owner_id != owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Host Owner scope does not match its Data workspace",
            )
        return workspace_status(operation_id=operation_id, result=result)

    @app.put("/api/local/v1/setup/workspace")
    async def initialize_workspace(
        payload: WorkspaceSetupRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, _session = await authenticated_controller(authorization)
        operation_id = await operation_id_for_host()
        try:
            result = await workspace.initialize(
                operation_id=operation_id,
                payload=payload.to_admin(),
            )
        except WorkspaceSetupError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        bound = await request_bootstrap(
            "controller.bind_owner",
            authentication=True,
            controller_id=principal["controller_id"],
            reset_epoch=principal["reset_epoch"],
            owner_id=result.owner.owner_id,
        )
        if bound.get("owner_id") != result.owner.owner_id:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap did not confirm the Data Owner scope",
            )
        return workspace_status(operation_id=operation_id, result=result)

    @app.get("/api/local/v1/system/state")
    async def system_state() -> dict:
        result = await request_bootstrap("health")
        return {
            "status": result["status"],
            "mode": result["mode"],
            "state": result["state"],
        }

    return app


def _bearer_token(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return ""
    return token
