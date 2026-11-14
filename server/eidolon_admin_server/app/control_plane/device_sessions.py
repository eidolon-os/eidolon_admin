"""Authorized single-device conversation entry, independent of group routing."""

from typing import ClassVar
from eidolon_sdk.biz.control.device_session import DeviceSessionRequest
from eidolon_sdk.device_foundation.v1 import BusinessOwnerId, OwnerDomainId
from .contracts import ControllerCommand


class ControllerDeviceSession(ControllerCommand):
    required_scope: ClassVar[str] = "device.conversation.control"
    business_owner_id: BusinessOwnerId
    request: DeviceSessionRequest

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.request.device.owner_domain_id
