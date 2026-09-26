"""Team commands carry Controller authority separately from scene configuration."""
from typing import ClassVar
from pydantic import Field
from eidolon_sdk.biz.control.coordination import CoordinationSelection
from eidolon_sdk.device_foundation.v1 import BusinessOwnerId, OwnerDomainId
from .contracts import ControllerCommand

class ControllerRoleGroupStart(ControllerCommand):
    required_scope: ClassVar[str] = "device.conversation.control"
    business_owner_id: BusinessOwnerId
    selection: CoordinationSelection

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.selection.input_device.owner_domain_id

class ControllerRoleGroupQuery(ControllerCommand):
    required_scope: ClassVar[str] = "device.conversation.control"
    business_owner_id: BusinessOwnerId
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.actor.owner_domain_id
