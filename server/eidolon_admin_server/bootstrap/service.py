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

from .commissioning_service import CommissioningService
from .config import (
    BootstrapMode,
    BootstrapSettings,
    CommissioningAdapter,
    NetworkAdapter,
)
from .identity import HostIdentityManager
from .controller_auth import (
    LOCAL_API_CONTROLLER_AUTH_PURPOSE,
    ControllerSignatureError,
    verify_controller_signature,
)
from .domain import ControllerGrant, ControllerRole, NetworkState, generate_setup_code
from .ports import (
    BootstrapStateConflict,
    BootstrapStateStore,
    NetworkProvisioning,
    NetworkProvisioningError,
)
from .tls_identity import CommissioningTlsIdentityManager


logger = logging.getLogger("eidolon.bootstrap.service")
_HOST_PROOF_PURPOSE = "eidolon-local-api-host-proof-v1"
_BASE64URL_32_BYTES = re.compile(r"^[A-Za-z0-9_-]{43}$")
_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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
        challenge = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .rstrip(b"=")
            .decode("ascii")
        )
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
            raise ControllerAuthenticationRejected(
                "Controller challenge does not match"
            )
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

    def bind_controller_owner(
        self,
        *,
        controller_id: str,
        reset_epoch: int,
        owner_id: str,
    ) -> dict[str, Any]:
        """Bind a Data-confirmed Owner scope to one current Controller grant."""

        grant = self._active_controller(controller_id)
        if not isinstance(reset_epoch, int) or grant.reset_epoch != reset_epoch:
            raise ControllerAuthenticationRejected(
                "Controller session is no longer authorized"
            )
        if not isinstance(owner_id, str) or not _OWNER_ID.fullmatch(owner_id):
            raise BootstrapOperationRejected("Owner scope is invalid")
        try:
            bound = self._store.bind_controller_owner(
                controller_id=controller_id,
                owner_id=owner_id,
                reset_epoch=reset_epoch,
                now=_timestamp(_now()),
            )
        except BootstrapStateConflict as exc:
            raise BootstrapOperationRejected(str(exc)) from exc
        return self._controller_principal(bound)

    def invite_controller(
        self, *, controller_id: str, ttl_seconds: int | None = None
    ) -> dict[str, Any]:
        """Open a short window in which one more phone may claim this Host.

        Asked for by a phone that already holds this Host, the way Matter has
        an existing administrator open a commissioning window with a freshly
        minted one-time secret. Before this, a second phone could be added no
        way but by revoking the first.
        """

        self._active_controller(controller_id)
        return self.issue_setup_code(ttl_seconds)

    def list_controllers(self, *, controller_id: str) -> dict[str, Any]:
        """Show every phone that holds this Host, to one that already does."""

        self._active_controller(controller_id)
        state = self._store.get_state()
        return {
            "controllers": [
                grant.to_dict()
                for grant in self._store.list_controllers()
                if grant.revoked_at is None and grant.reset_epoch == state.reset_epoch
            ]
        }

    def revoke_controller(self, *, controller_id: str, target_id: str) -> dict[str, Any]:
        """Withdraw one peer's authority at the request of another.

        A phone may revoke itself; what it may not do is leave the Host with
        nobody, which is controller-reset's job and the operator's to ask for.
        """

        self._active_controller(controller_id)
        try:
            revoked = self._store.revoke_controller(
                controller_id=target_id,
                now=_timestamp(_now()),
            )
        except BootstrapStateConflict as exc:
            raise BootstrapOperationRejected(str(exc)) from exc
        logger.info(
            "controller revoked by=%s target=%s",
            controller_id,
            target_id,
        )
        return {"controller": revoked.to_dict()}

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

    def _controller_principal(self, grant: ControllerGrant) -> dict[str, Any]:
        state = self._store.get_state()
        return {
            "contract_version": "1",
            "controller_id": grant.controller_id,
            "role": grant.role.value,
            "display_name": grant.display_name,
            "platform": grant.platform,
            "reset_epoch": grant.reset_epoch,
            "owner_id": state.owner_id,
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
            "setup_session": self._active_setup_session(),
        }
        return {**unsigned, "signature": self._identity_manager.sign_mapping(unsigned)}

    def development_lan_commissioning_endpoint(self) -> dict[str, Any]:
        """Expose the signed endpoint for an already-networked development Host.

        This is the LAN transport equivalent of BlueZ's public Info
        characteristic. It remains development-only and does not authorize a
        mutation by itself.
        """

        self._require_development_lan_commissioning()
        return self.commissioning_endpoint()

    def claim_development_lan_controller(
        self,
        *,
        commissioning_id: str,
        setup_code: str,
        controller: dict[str, Any],
    ) -> dict[str, Any]:
        """Claim an already-networked development Host through the Local API.

        Authorization and the atomic Grant/session consumption are delegated
        to the same CommissioningService used by the BLE protocol.
        """

        self._require_development_lan_commissioning()
        if self._network is None:
            raise BootstrapOperationRejected(
                "development LAN commissioning requires a network adapter"
            )
        commissioning = CommissioningService(store=self._store, network=self._network)
        authorization = commissioning.authorize(
            session_id=commissioning_id,
            secret=setup_code,
        )
        # Reaching this pinned Local API proves that the development Host is
        # already on the caller's LAN. The memory adapter has no OS network
        # state to discover, so publish that fact before the normal atomic
        # claim transition.
        self.reconcile_network_state(NetworkState.CONNECTED)
        result = commissioning.claim_controller(authorization, controller)
        return {
            "contract_version": "1",
            "operation": "local.development-lan-commissioning-claim",
            "host_id": self._identity_manager.identity.host_id,
            **result,
        }

    def _require_development_lan_commissioning(self) -> None:
        if (
            self._settings.mode is not BootstrapMode.DEVELOPMENT
            or self._settings.commissioning_adapter is not CommissioningAdapter.DISABLED
            or self._settings.network_adapter is not NetworkAdapter.MEMORY
        ):
            raise BootstrapOperationRejected(
                "development LAN commissioning is unavailable on this Host"
            )

    def _active_setup_session(self) -> dict[str, str] | None:
        """Which setup session, if any, a phone may present a code against.

        The code and the session that accepts it are two halves of one act, and
        a phone is told only the half that is not a secret. Reporting this in
        development only meant an operator could mint a code for a shipped Host
        and the phone would still have nowhere to spend it.

        Reporting is unconditional; creating one is not. A development Host may
        conjure a session from its fixed code so a workstation loop keeps
        working, and every other Host waits to be given one.
        """

        session = self._store.latest_commissioning_session()
        now = _timestamp(_now())
        if (
            session is None
            or session.consumed_at is not None
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            if self._settings.mode is not BootstrapMode.DEVELOPMENT:
                return None
            fixed_code = self._settings.dev_setup_code
            state = self._store.get_state()
            if fixed_code is None or state.claim_state.value != "unclaimed":
                return None
            self._issue_setup_code(
                fixed_code,
                self._settings.dev_setup_code_ttl_seconds,
            )
            session = self._store.latest_commissioning_session()
            assert session is not None
        return {
            "commissioning_id": session.session_id,
            "expires_at": session.expires_at,
        }

    def issue_setup_code(self, ttl_seconds: int | None = None) -> dict[str, Any]:
        """Mint the one-time code a phone types to claim this Host.

        Reaching this operation already means holding the Host's own control
        socket, which is root-owned and local. That is the same authority
        ``controller-reset`` runs under, and issuing a code is the lesser act
        of the two — so gating it on the build being a development one only
        ever meant a shipped Host could not be claimed at all.

        A production Host draws a fresh code every time. A development one may
        pin a fixed code so a workstation loop does not reprint it.
        """

        ttl = ttl_seconds or self._settings.dev_setup_code_ttl_seconds
        if not 60 <= ttl <= 86400:
            raise BootstrapOperationRejected("ttl_seconds must be between 60 and 86400")
        setup_code = self._settings.dev_setup_code or generate_setup_code()
        return self._issue_setup_code(setup_code, ttl)

    def _issue_setup_code(
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

    def setup_session_status(self) -> dict[str, Any]:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development Setup status is disabled in production"
            )
        session = self._store.latest_commissioning_session()
        return {"current": None if session is None else session.to_dict()}

    def reset_controllers(self) -> dict[str, Any]:
        """Revoke every Controller Grant so a new phone can claim this Host again.

        This is the recovery path for an Owner who lost every managing phone. It
        keeps the Host identity, the Owner binding, saved Wi-Fi and every
        sibling service's data; only the authority to manage this Host is
        withdrawn, so the normal first-claim flow can run again.
        """

        before = self._store.get_state()
        revoked = [
            grant.controller_id
            for grant in self._store.list_controllers()
            if grant.revoked_at is None
        ]
        after = self._store.reset_authority(
            network_state=before.network_state,
            now=_timestamp(_now()),
        )
        logger.warning(
            "controller authority reset reset_epoch=%s owner_id=%s revoked=%s",
            after.reset_epoch,
            after.owner_id,
            len(revoked),
        )
        return {
            "host_id": self._identity_manager.identity.host_id,
            "before": before.to_dict(),
            "after": after.to_dict(),
            "revoked_controllers": revoked,
            "preserved": [
                "host_identity",
                "owner_binding",
                "network_profiles",
                "component_data",
            ],
        }

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

        setup = self._active_setup_session()
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
            "setup_session": setup,
        }
