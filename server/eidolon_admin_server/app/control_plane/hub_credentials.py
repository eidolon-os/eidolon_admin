"""Short-lived Admin credentials for trusted Admin-to-Hub mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from eidolon_sdk.device_foundation.v1 import BusinessOwnerId, ControllerActorRef

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

    def issue_admission_context(
        self,
        *,
        actor: ControllerActorRef,
        business_owner_id: BusinessOwnerId,
        intent_id: str | None = None,
    ) -> str:
        """Bind one short-lived Hub call to the authenticated Controller context."""

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
                "sub": "eidolon-admin/admission-consumer",
                "presenter": "eidolon-admin/admission-consumer",
                "aud": self.audience,
                "actor": actor.model_dump(mode="json"),
                "owner_domain_id": str(actor.owner_domain_id),
                "business_owner_id": str(business_owner_id),
                "scopes": list(actor.granted_scopes),
                **({"intent_id": intent_id} if intent_id is not None else {}),
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
            },
            self.secret,
            algorithm="HS256",
        )
        return f"Bearer {token}"

    def issue_removal_intent(
        self,
        *,
        controller_id: str,
        intent_id: str,
        device_ref: DeviceRef,
        business_owner_id: BusinessOwnerId,
    ) -> str:
        """The credential a Claim revocation is presented with.

        Revocation is an Admission route, so it is presented to Hub's Admission
        authorizer and has to speak that vocabulary. This used to mint a
        different one — ``actor_ref`` as a bare string, ``owner_id`` instead of
        ``owner_domain_id``, no ``business_owner_id`` — which is the Management
        surface's vocabulary, correct for reading and renaming devices and
        wrong for this route. Hub read ``claims["actor"]``, raised KeyError, and
        answered 401 for every removal anyone ever attempted.

        The generation binding is not carried here: on the Admission surface it
        travels in the DeviceRef of the command itself, where Hub compares it
        against the stored Claim. Putting it in the credential too would be a
        second copy of the fact that decides the same thing.

        The business Owner is passed rather than derived. ``owner_...`` and
        ``owner-...`` are two different identities — who the Owner is, and which
        Owner Domain this Host serves — and deriving one from the other is how
        two facts become one wrong one.
        """

        actor = ControllerActorRef(
            principal_id=controller_id,
            principal_type="controller",
            owner_domain_id=device_ref.owner_domain_id,
            granted_scopes=("device.claim.revoke",),
            authentication_strength="software",
        )
        return self.issue_admission_context(
            actor=actor,
            business_owner_id=business_owner_id,
            intent_id=intent_id,
        )
