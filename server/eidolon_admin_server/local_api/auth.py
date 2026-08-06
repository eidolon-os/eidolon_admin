"""Process-local opaque sessions for the Local API ingress."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LocalControllerSession:
    token_hash: str
    controller_id: str
    reset_epoch: int
    principal: dict[str, Any]
    expires_at: datetime


class LocalControllerSessionStore:
    """Short-lived sessions; Controller grants remain authoritative in Bootstrap."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._sessions: dict[str, LocalControllerSession] = {}

    def issue(self, principal: dict[str, Any]) -> tuple[str, LocalControllerSession]:
        controller_id = principal.get("controller_id")
        reset_epoch = principal.get("reset_epoch")
        if not isinstance(controller_id, str) or not isinstance(reset_epoch, int):
            raise ValueError("Bootstrap returned an invalid Controller principal")
        now = self._clock()
        self._sessions = {
            token_hash: session
            for token_hash, session in self._sessions.items()
            if session.expires_at > now
        }
        if len(self._sessions) >= 1024:
            oldest = min(self._sessions, key=lambda value: self._sessions[value].expires_at)
            self._sessions.pop(oldest, None)
        token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        token_hash = self._hash(token)
        session = LocalControllerSession(
            token_hash=token_hash,
            controller_id=controller_id,
            reset_epoch=reset_epoch,
            principal=dict(principal),
            expires_at=now + self._ttl,
        )
        self._sessions[token_hash] = session
        return token, session

    def get(self, token: str) -> LocalControllerSession | None:
        if not isinstance(token, str) or len(token) != 43:
            return None
        token_hash = self._hash(token)
        session = self._sessions.get(token_hash)
        if session is None:
            return None
        if session.expires_at <= self._clock():
            self._sessions.pop(token_hash, None)
            return None
        return session

    def revoke(self, session: LocalControllerSession) -> None:
        self._sessions.pop(session.token_hash, None)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
