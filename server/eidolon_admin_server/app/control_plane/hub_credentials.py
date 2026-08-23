"""Short-lived Admin credentials for trusted Admin-to-Hub mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .contracts import DeviceRef
from .errors import AuthorityFailure


@dataclass(frozen=True, slots=True)
class HubAdminCredentialIssuer:
    """Mint a short-lived credential for Admin's human-approved workflow.

    The shared HS256 key is an installation secret shared only by Admin and
    Hub. Local API and Mobile receive neither the key nor the resulting JWT.
    """

    secret: bytes
    ttl_seconds: int = 60
    audience: str = "eidolon-admission"

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

    def issue_removal_discovery(
        self, *, controller_id: str, owner_id: str, device_id: str
    ) -> str:
        return self._issue_scoped(
            controller_id=controller_id,
            owner_id=owner_id,
            scopes=["device.read"],
            target_device_id=device_id,
        )

    def issue_removal_intent(
        self,
        *,
        controller_id: str,
        intent_id: str,
        device_ref: DeviceRef,
    ) -> str:
        return self._issue_scoped(
            controller_id=controller_id,
            owner_id=device_ref.owner_domain_id,
            scopes=["device.claim.revoke"],
            target_device_id=device_ref.device_instance_id,
            intent_id=intent_id,
            target_owner_domain_generation=device_ref.owner_domain_generation,
            target_claim_generation=device_ref.claim_generation,
            target_trust_epoch=device_ref.trust_epoch,
            target_manifest_digest=device_ref.accepted_manifest_digest,
        )

    def _issue_scoped(
        self,
        *,
        controller_id: str,
        owner_id: str,
        scopes: list[str],
        target_device_id: str,
        intent_id: str | None = None,
        target_owner_domain_generation: int | None = None,
        target_claim_generation: int | None = None,
        target_trust_epoch: int | None = None,
        target_manifest_digest: str | None = None,
    ) -> str:
        if len(self.secret) < 32:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is not configured",
                503,
            )
        now = datetime.now(UTC)
        subject = "eidolon-admin/lifecycle-workflow"
        claims = {
            "sub": subject,
            "presenter": subject,
            "actor_ref": f"controller:{controller_id}",
            "aud": self.audience,
            "roles": ["device-manager"],
            "scopes": scopes,
            "owner_id": owner_id,
            "target_device_id": target_device_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
        }
        if intent_id is not None:
            claims["intent_id"] = intent_id
        if target_owner_domain_generation is not None:
            claims["target_owner_domain_generation"] = (
                target_owner_domain_generation
            )
        if target_claim_generation is not None:
            claims["target_claim_generation"] = target_claim_generation
        if target_trust_epoch is not None:
            claims["target_trust_epoch"] = target_trust_epoch
        if target_manifest_digest is not None:
            claims["target_manifest_digest"] = target_manifest_digest
        return "Bearer " + jwt.encode(claims, self.secret, algorithm="HS256")
