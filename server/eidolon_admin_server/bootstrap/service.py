"""Application service for safe early-boot operations."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import BootstrapMode, BootstrapSettings
from .identity import HostIdentityManager
from .domain import NetworkState
from .ports import BootstrapStateStore
from .tls_identity import CommissioningTlsIdentityManager


logger = logging.getLogger("eidolon.bootstrap.service")
_HOST_PROOF_PURPOSE = "eidolon-local-api-host-proof-v1"
_BASE64URL_32_BYTES = re.compile(r"^[A-Za-z0-9_-]{43}$")


class BootstrapOperationRejected(RuntimeError):
    """The requested operation is not allowed in the current trust mode."""


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
    ) -> None:
        self._settings = settings
        self._store = store
        self._identity_manager = identity_manager
        self._tls_identity_manager = (
            tls_identity_manager
            or CommissioningTlsIdentityManager(settings.commissioning_tls_pem_path)
        )
        self._run_id: str | None = None
        self._started_at: str | None = None
        self._commissioning_status = (
            "disabled"
            if settings.commissioning_adapter.value == "disabled"
            else "starting"
        )

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

    def commissioning_endpoint(self) -> dict[str, Any]:
        """Signed dynamic endpoint data readable before the pinned TLS handshake."""

        unsigned = {
            **self.public_descriptor(),
            "purpose": "eidolon-ble-commissioning-endpoint-v1",
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
            return None
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

        now = _now()
        session_id = str(uuid.uuid4())
        setup_code = f"{secrets.randbelow(1_000_000):06d}"
        result = {
            "host_id": self._identity_manager.identity.host_id,
            "commissioning_id": session_id,
            "setup_code": setup_code,
            "issued_at": _timestamp(now),
            "expires_at": _timestamp(now + timedelta(seconds=ttl)),
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
