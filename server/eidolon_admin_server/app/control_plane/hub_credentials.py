"""Short-lived Admin credentials for trusted Admin-to-Hub mutations."""

from __future__ import annotations

from dataclasses import dataclass

from eidolon_sdk.device_foundation.v1 import (
    AdmissionCredential,
    AdmissionCredentialError,
    BusinessOwnerId,
    ControllerActorRef,
    issue_admission_credential,
)

from .contracts import DeviceRef
from .errors import AuthorityFailure

#: Who Hub is told is presenting the credential.
#:
#: One subject for every Admission mutation Admin makes on an Owner's behalf.
#: Which Controller asked, and about what, travels in the actor and the command
#: — not in the subject, which says only which process is holding the key.
_SUBJECT = "eidolon-admin/admission-consumer"


@dataclass(frozen=True, slots=True)
class HubAdminCredentialIssuer:
    """Mint a short-lived credential for Admin's human-approved workflow.

    The shared HS256 key is an installation secret shared only by Admin and
    Hub. Local API and Mobile receive neither the key nor the resulting JWT.

    What the credential carries is the SDK's definition, which Hub reads with
    the same code. This file used to build the claim dictionary by hand, and a
    second method in it built a different one for the same Hub — the removal
    path minted Hub's device-management vocabulary and presented it to
    Admission, which answered 401 for every device removal ever attempted.
    """

    secret: bytes
    ttl_seconds: int = 60

    def issue_admission_context(
        self,
        *,
        actor: ControllerActorRef,
        business_owner_id: BusinessOwnerId,
        intent_id: str | None = None,
        target_device_ref: DeviceRef | None = None,
    ) -> str:
        """Bind one short-lived Hub call to the authenticated Controller context."""

        try:
            return issue_admission_credential(
                AdmissionCredential(
                    subject=_SUBJECT,
                    actor=actor,
                    owner_domain_id=actor.owner_domain_id,
                    business_owner_id=business_owner_id,
                    scopes=tuple(actor.granted_scopes),
                    intent_id=intent_id,
                    target_device_ref=target_device_ref,
                ),
                secret=self.secret,
                ttl_seconds=self.ttl_seconds,
            )
        except AdmissionCredentialError as exc:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is not configured",
                503,
            ) from exc

    def issue_removal_intent(
        self,
        *,
        controller_id: str,
        intent_id: str,
        device_ref: DeviceRef,
        business_owner_id: BusinessOwnerId,
    ) -> str:
        """The credential a Claim revocation is presented with.

        Revocation is an Admission route, so this is an Admission credential —
        the same one every other Admission mutation uses. It is not a variant.

        The same credential is presented to two Hub surfaces during one removal:
        the Claim revocation, and the read of the device-local erase operation.
        They enforce different things from it — revocation checks the command's
        DeviceRef against the stored Claim, the erase read fences the request
        against ``target_device_ref`` — but they read one credential. Two
        vocabularies for one authorization is what produced the 401.

        The business Owner is passed rather than derived. ``owner_...`` and
        ``owner-...`` are two different identities — who the Owner is, and which
        Owner Domain this Host serves — and deriving one from the other is how
        two facts become one wrong one.
        """

        return self.issue_admission_context(
            actor=ControllerActorRef(
                principal_id=controller_id,
                principal_type="controller",
                owner_domain_id=device_ref.owner_domain_id,
                granted_scopes=("device.claim.revoke",),
                authentication_strength="software",
            ),
            business_owner_id=business_owner_id,
            intent_id=intent_id,
            target_device_ref=device_ref,
        )
