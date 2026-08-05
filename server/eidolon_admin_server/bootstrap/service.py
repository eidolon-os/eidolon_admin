"""Application service for safe early-boot operations."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import BootstrapMode, BootstrapSettings
from .identity import HostIdentityManager
from .ports import BootstrapStateStore


logger = logging.getLogger("eidolon.bootstrap.service")


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
    ) -> None:
        self._settings = settings
        self._store = store
        self._identity_manager = identity_manager
        self._run_id: str | None = None
        self._started_at: str | None = None

    def initialize(self) -> None:
        now = _timestamp(_now())
        self._store.open()
        self._store.initialize(now)
        self._identity_manager.load()
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
            "descriptor": self.public_descriptor(),
            "state": self._store.get_state().to_dict(),
        }

    def issue_development_descriptor(
        self, ttl_seconds: int | None = None
    ) -> dict[str, Any]:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development descriptor issuance is disabled in production"
            )
        ttl = ttl_seconds or self._settings.dev_descriptor_ttl_seconds
        if not 60 <= ttl <= 86400:
            raise BootstrapOperationRejected("ttl_seconds must be between 60 and 86400")

        now = _now()
        session_id = str(uuid.uuid4())
        secret = secrets.token_urlsafe(24)
        unsigned = {
            **self.public_descriptor(),
            "mode": BootstrapMode.DEVELOPMENT.value,
            "commissioning_id": session_id,
            "commissioning_secret": secret,
            "issued_at": _timestamp(now),
            "expires_at": _timestamp(now + timedelta(seconds=ttl)),
        }
        signature = self._identity_manager.sign_mapping(unsigned)
        self._store.issue_commissioning_session(
            session_id=session_id,
            secret_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
            created_at=unsigned["issued_at"],
            expires_at=unsigned["expires_at"],
        )
        return {**unsigned, "signature": signature}

    def development_descriptor_status(self) -> dict[str, Any]:
        if self._settings.mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapOperationRejected(
                "development descriptor status is disabled in production"
            )
        session = self._store.latest_commissioning_session()
        return {"current": None if session is None else session.to_dict()}
