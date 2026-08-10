"""Short-lived Admin credentials for trusted Admin-to-Hub mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .errors import AuthorityFailure


@dataclass(frozen=True, slots=True)
class HubAdminCredentialIssuer:
    """Mint a short-lived credential for Admin's human-approved workflow.

    The shared HS256 key is an installation secret shared only by Admin and
    Hub. Local API and Mobile receive neither the key nor the resulting JWT.
    """

    secret: bytes
    ttl_seconds: int = 60
    audience: str = "eidolon-hub"

    def issue(self, *, controller_id: str) -> str:
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
                "roles": ["hub-admin"],
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
            },
            self.secret,
            algorithm="HS256",
        )
        return f"Bearer {token}"
