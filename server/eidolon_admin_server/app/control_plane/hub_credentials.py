"""Short-lived Owner credentials for Admin-to-Hub management calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .errors import AuthorityFailure


@dataclass(frozen=True, slots=True)
class HubOwnerCredentialIssuer:
    """Mint the narrow credential required by Hub's pairing-claim contract.

    The shared HS256 key is an installation secret shared only by Admin and
    Hub. Mobile receives neither the key nor the resulting short-lived JWT.
    """

    secret: bytes
    ttl_seconds: int = 60
    audience: str = "eidolon-hub"

    def issue(self, *, owner_id: str, controller_id: str) -> str:
        if len(self.secret) < 32:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is not configured",
                503,
            )
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": f"eidolon-local-api/{controller_id}",
                "aud": self.audience,
                "roles": ["device-manager"],
                "owner_id": owner_id,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
            },
            self.secret,
            algorithm="HS256",
        )
        return f"Bearer {token}"
