"""Runtime identity resolve schemas."""

from __future__ import annotations

from eidolon_sdk.biz.persona import ResolvedRuntimeIdentity
from pydantic import BaseModel


ResolvedContext = ResolvedRuntimeIdentity


class ResolveResponse(BaseModel):
    context: ResolvedContext
