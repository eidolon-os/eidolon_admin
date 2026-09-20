"""Content-bound policy for Data workspace initialization operations."""

from __future__ import annotations

import hashlib
import json

from .contracts import WorkspaceInitializeRequest


def workspace_request_fingerprint(payload: WorkspaceInitializeRequest) -> str:
    """Match Data's canonical request fingerprint without importing Data code."""

    document = payload.model_dump(mode="json")
    # Omitted optional authoring preserves receipts written by older Hosts.
    # Keep nested fields intact: the complete persona participates in identity.
    for key in ("persona", "preferences", "source_preset_id", "source_preset_revision"):
        if document[key] is None:
            del document[key]
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
