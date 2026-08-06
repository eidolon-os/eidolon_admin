"""Application service for safe early-boot operations."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import BootstrapMode, BootstrapSettings
from .identity import HostIdentityManager
from .controller_auth import (
    LOCAL_API_CONTROLLER_AUTH_PURPOSE,
    ControllerSignatureError,
    verify_controller_signature,
)
from .domain import ControllerGrant, ControllerRole, NetworkState
from .ports import (
    BootstrapStateStore,
    NetworkProvisioning,
    NetworkProvisioningError,
)
from .tls_identity import CommissioningTlsIdentityManager


logger = logging.getLogger("eidolon.bootstrap.service")
_HOST_PROOF_PURPOSE = "eidolon-local-api-host-proof-v1"
_BASE64URL_32_BYTES = re.compile(r"^[A-Za-z0-9_-]{43}$")


class BootstrapOperationRejected(RuntimeError):
    """The requested operation is not allowed in the current trust mode."""


class ControllerAuthenticationRejected(BootstrapOperationRejected):
    """A LAN caller did not prove an active Host Controller grant."""


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class BootstrapService:
    def __init__(
        self,
        *,
        settings: BootstrapSettings,
        store: BootstrapStateStore,
        identity_manager: HostIdentityManager,
        tls_identity_manager: CommissioningTlsIdentityManager | None = None,
        network: NetworkProvisioning | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._identity_manager = identity_manager
        self._tls_identity_manager = (
            tls_identity_manager
            or CommissioningTlsIdentityManager(settings.commissioning_tls_pem_path)
        )
        self._network = network
        self._run_id: str | None = None
        self._started_at: str | None = None
        self._commissioning_status = (
            "disabled"
            if settings.commissioning_adapter.value == "disabled"
            else "starting"
        )
        self._controller_challenges: dict[str, tuple[str, int, float]] = {}

    def initialize(self) -> None:
        now = _timestamp(_now())
        self._store.open()
        self._store.initialize(now)
        host_identity = self._identity_manager.load()
        self._tls_identity_manager.load(host_identity.host_id)
        interrupted = self._store.fail_interrupted_operations(now=now)
        if interrupted:
            logger.warning(
                "marked %s interrupted bootstrap operation(s) failed",
                interrupted,
            )
        self._run_id = str(uuid.uuid4())
        self._started_at = now
        logger.info(
            "bootstrap service initialized run_id=%s pid=%s",
            self._run_id,
            os.getpid(),
        )

    def shutdown(self) -> None:
        if self._run_id is not None:
            logger.info(
                "bootstrap service stopping run_id=%s pid=%s",
                self._run_id,
                os.getpid(),
            )
        self._store.close()
        self._controller_challenges.clear()
        self._run_id = None

    def public_descriptor(self) -> dict[str, Any]:
        identity = self._identity_manager.identity
        return {
            "contract_version": "1",
            "host_id": identity.host_id,
            "host_public_key": identity.public_key,
            "host_public_key_fingerprint": identity.public_key_fingerprint,
            "ble_service_uuid": self._settings.ble_service_uuid,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "running",
            "mode": self._settings.mode.value,
            "pid": os.getpid(),
            "run_id": self._run_id,
            "started_at": self._started_at,
            "commissioning": {
                "adapter": self._settings.commissioning_adapter.value,
                "status": self._commissioning_status,
            },
            "descriptor": self.public_descriptor(),
            "state": self._store.get_state().to_dict(),
        }

    def set_commissioning_status(self, status: str) -> None:
        if status not in {"disabled", "starting", "ready", "degraded", "stopping"}:
            raise ValueError("unknown commissioning runtime status")
        self._commissioning_status = status

    def reconcile_network_state(self, network_state: NetworkState) -> None:
        """Publish the system adapter's post-recovery state as Bootstrap truth."""

        self._store.reconcile_network_state(
            network_state=network_state,
            now=_timestamp(_now()),
        )

    def prove_host(self, challenge: str) -> dict[str, Any]:
        """Prove possession of the Host key for a caller-provided nonce.

        The purpose field domain-separates this proof from descriptors and any
        future authentication signatures. The random challenge supplies replay
        protection, so this operation does not create durable state.
        """

        if not isinstance(challenge, str) or not _BASE64URL_32_BYTES.fullmatch(
            challenge
        ):
            raise BootstrapOperationRejected(
                "host proof challenge must be 32 unpadded base64url bytes"
            )
        try:
            decoded = base64.urlsafe_b64decode(challenge + "=")
        except ValueError as exc:
            raise BootstrapOperationRejected(
                "host proof challenge is not valid base64url"
            ) from exc
        if len(decoded) != 32:
            raise BootstrapOperationRejected(
                "host proof challenge must decode to 32 bytes"
            )
        canonical_challenge = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode()
        if canonical_challenge != challenge:
            raise BootstrapOperationRejected(
                "host proof challenge must use canonical base64url encoding"
            )

        unsigned = {
            "contract_version": "1",
            "purpose": _HOST_PROOF_PURPOSE,
            "host_id": self._identity_manager.identity.host_id,
            "challenge": challenge,
        }
        return {
            **unsigned,
            "signature": self._identity_manager.sign_mapping(unsigned),
        }

    def issue_controller_challenge(self, controller_id: str) -> dict[str, Any]:
        """Issue a bounded, one-time LAN authentication challenge."""

        grant = self._active_controller(controller_id)
        now = time.monotonic()
        self._purge_controller_challenges(now)
        if len(self._controller_challenges) >= 256:
            oldest = min(
                self._controller_challenges,
                key=lambda value: self._controller_challenges[value][2],
            )
            self._controller_challenges.pop(oldest, None)
        challenge = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(
            b"="
        ).decode("ascii")
        self._controller_challenges[challenge] = (
            grant.controller_id,
            grant.reset_epoch,
            now + 60,
        )
        return {
            "contract_version": "1",
            "purpose": LOCAL_API_CONTROLLER_AUTH_PURPOSE,
            "controller_id": grant.controller_id,
            "challenge": challenge,
            "reset_epoch": grant.reset_epoch,
        }

    def authenticate_controller(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Consume a LAN challenge and return a secret-free Controller principal."""

        if not isinstance(payload, dict):
            raise ControllerAuthenticationRejected("Controller proof is invalid")
        challenge = payload.get("challenge")
        if not isinstance(challenge, str):
            raise ControllerAuthenticationRejected("Controller proof is invalid")
        now = time.monotonic()
        self._purge_controller_challenges(now)
        record = self._controller_challenges.pop(challenge, None)
        if record is None or record[2] <= now:
            raise ControllerAuthenticationRejected(
                "Controller challenge is missing or expired"
            )
        controller_id, reset_epoch, _ = record
        if (
            payload.get("contract_version") != "1"
            or payload.get("purpose") != LOCAL_API_CONTROLLER_AUTH_PURPOSE
            or payload.get("controller_id") != controller_id
            or payload.get("reset_epoch") != reset_epoch
        ):
            raise ControllerAuthenticationRejected("Controller challenge does not match")
        grant = self._active_controller(controller_id)
        try:
            verify_controller_signature(
                grant,
                challenge=challenge,
                purpose=LOCAL_API_CONTROLLER_AUTH_PURPOSE,
                reset_epoch=reset_epoch,
                signature_value=payload.get("signature"),
            )
        except ControllerSignatureError as exc:
            raise ControllerAuthenticationRejected(
                "Controller signature is invalid"
            ) from exc
        return self._controller_principal(grant)

    def validate_controller(
        self, controller_id: str, reset_epoch: int
    ) -> dict[str, Any]:
        """Revalidate a Local API session against current Bootstrap authority."""

        grant = self._active_controller(controller_id)
        if not isinstance(reset_epoch, int) or grant.reset_epoch != reset_epoch:
            raise ControllerAuthenticationRejected(
                "Controller session is no longer authorized"
            )
        return self._controller_principal(grant)

    def _active_controller(self, controller_id: str) -> ControllerGrant:
        if not isinstance(controller_id, str) or not re.fullmatch(
            r"ectrl-[0-9a-f]{20}", controller_id
        ):
            raise ControllerAuthenticationRejected(
                "Controller is not authorized for this Host"
            )
        grant = self._store.get_controller(controller_id)
        state = self._store.get_state()
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.reset_epoch != state.reset_epoch
            or grant.role is not ControllerRole.HOST_ADMIN
        ):
            raise ControllerAuthenticationRejected(
                "Controller is not authorized for this Host"
            )
        return grant

    @staticmethod
    def _controller_principal(grant: ControllerGrant) -> dict[str, Any]:
        return {
            "contract_version": "1",
            "controller_id": grant.controller_id,
            "role": grant.role.value,
            "display_name": grant.display_name,
            "platform": grant.platform,
            "reset_epoch": grant.reset_epoch,
        }

    def _purge_controller_challenges(self, now: float) -> None:
        expired = [
            challenge
            for challenge, (_, _, expires_at) in self._controller_challenges.items()
            if expires_at <= now
        ]
        for challenge in expired:
            self._controller_challenges.pop(challenge, None)

    def commissioning_endpoint(self) -> dict[str, Any]:
        """Signed dynamic endpoint data readable before the pinned TLS handshake."""

        unsigned = {
            "contract_version": "1",
            "purpose": "eidolon-ble-commissioning-endpoint-v1",
            "host_public_key": self._identity_manager.identity.public_key,
            "reset_epoch": self._store.get_state().reset_epoch,
            "tls_spki_fingerprint": self._tls_identity_manager.identity.spki_fingerprint,
            "development_setup": self._active_development_setup(),
        }
        return {**unsigned, "signature": self._identity_manager.sign_mapping(unsigned)}

    def _active_development_setup(self) -> dict[str, str] | None:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            return None
        session = self._store.latest_commissioning_session()
        now = _timestamp(_now())
        if (
            session is None
            or session.consumed_at is not None
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            fixed_code = self._settings.dev_setup_code
            state = self._store.get_state()
            if fixed_code is None or state.claim_state.value != "unclaimed":
                return None
            self._issue_development_setup_code(
                fixed_code,
                self._settings.dev_setup_code_ttl_seconds,
            )
            session = self._store.latest_commissioning_session()
            assert session is not None
        return {
            "commissioning_id": session.session_id,
            "expires_at": session.expires_at,
        }

    def issue_development_setup_code(
        self, ttl_seconds: int | None = None
    ) -> dict[str, Any]:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development Setup code issuance is disabled in production"
            )
        ttl = ttl_seconds or self._settings.dev_setup_code_ttl_seconds
        if not 60 <= ttl <= 86400:
            raise BootstrapOperationRejected("ttl_seconds must be between 60 and 86400")

        setup_code = self._settings.dev_setup_code or (
            f"{secrets.randbelow(1_000_000):06d}"
        )
        return self._issue_development_setup_code(setup_code, ttl)

    def _issue_development_setup_code(
        self,
        setup_code: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        now = _now()
        session_id = str(uuid.uuid4())
        result = {
            "host_id": self._identity_manager.identity.host_id,
            "commissioning_id": session_id,
            "setup_code": setup_code,
            "issued_at": _timestamp(now),
            "expires_at": _timestamp(now + timedelta(seconds=ttl_seconds)),
        }
        self._store.issue_commissioning_session(
            session_id=session_id,
            secret_hash=hashlib.sha256(setup_code.encode("utf-8")).hexdigest(),
            created_at=result["issued_at"],
            expires_at=result["expires_at"],
        )
        return result

    def development_setup_status(self) -> dict[str, Any]:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development Setup status is disabled in production"
            )
        session = self._store.latest_commissioning_session()
        return {"current": None if session is None else session.to_dict()}

    async def reset_development_state(
        self,
        *,
        forget_wifi_profiles: bool,
    ) -> dict[str, Any]:
        """Reset commissioning authority without replacing the stable Host identity."""

        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development reset is disabled in production"
            )
        if self._network is None:
            raise BootstrapOperationRejected(
                "development reset requires a network provisioning adapter"
            )
        try:
            network = await self._network.get_state()
        except NetworkProvisioningError as exc:
            raise BootstrapOperationRejected(
                "development reset could not read the current network state"
            ) from exc

        before = self._store.get_state()
        after = self._store.reset_authority(
            network_state=network.state,
            now=_timestamp(_now()),
        )
        if forget_wifi_profiles:
            try:
                network = await self._network.forget_all_wifi_profiles()
            except NetworkProvisioningError as exc:
                try:
                    current = await self._network.get_state()
                    self.reconcile_network_state(current.state)
                except NetworkProvisioningError:
                    pass
                raise BootstrapOperationRejected(
                    "claim reset completed, but saved Wi-Fi profiles could not be cleared"
                ) from exc
            self.reconcile_network_state(network.state)
            after = self._store.get_state()

        setup = self._active_development_setup()
        logger.warning(
            "development authority reset reset_epoch=%s forget_wifi_profiles=%s",
            after.reset_epoch,
            forget_wifi_profiles,
        )
        return {
            "host_id": self._identity_manager.identity.host_id,
            "before": before.to_dict(),
            "after": after.to_dict(),
            "forgot_wifi_profiles": forget_wifi_profiles,
            "development_setup": setup,
        }
