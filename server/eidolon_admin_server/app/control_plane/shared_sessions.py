"""Shared transport commands carried by the existing Controller authority."""

from typing import ClassVar
from pydantic import Field
from eidolon_sdk.biz.control.shared_session import SharedSessionSelection
from eidolon_sdk.device_foundation.v1 import BusinessOwnerId, OwnerDomainId
from .contracts import ControllerCommand


class ControllerSharedStart(ControllerCommand):
    required_scope: ClassVar[str] = "device.shared-session.control"
    business_owner_id: BusinessOwnerId
    selection: SharedSessionSelection

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.selection.devices[0].owner_domain_id


class ControllerSharedClose(ControllerCommand):
    required_scope: ClassVar[str] = "device.shared-session.control"
    business_owner_id: BusinessOwnerId
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.actor.owner_domain_id
