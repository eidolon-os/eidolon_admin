"""Content-bound policy for Data workspace initialization operations."""

from __future__ import annotations

import hashlib
import json

from .contracts import WorkspaceInitializeRequest


def workspace_request_fingerprint(payload: WorkspaceInitializeRequest) -> str:
    """Match Data's canonical request fingerprint without importing Data code."""

    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
