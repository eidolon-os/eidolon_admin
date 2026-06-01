"""Error / envelope helpers shared across registry modules."""
from __future__ import annotations

import json


def unwrap_detail(message: str) -> str:
    """Pull the inner ``detail`` string out of FastAPI's
    ``{"detail": "..."}`` envelope if that's what ``message`` looks like.

    Why this exists:
        Every sub-project (memory, agent, hub) uses FastAPI's
        ``HTTPException``, which serialises its detail into the JSON
        body ``{"detail": "..."}``. When admin's repository captures
        ``response.text`` and we then wrap our own HTTPException around
        it, we double-wrap into ``{"detail":"{\"detail\":\"...\"}"}``.

        Applying this helper at the orchestrator's error-mapping step
        makes the wrap idempotent — callers see one clean detail string.

    Robust to non-JSON inputs (returns them unchanged) so non-FastAPI
    upstreams can pass through without churn.
    """
    if not message:
        return message
    try:
        parsed = json.loads(message)
    except (json.JSONDecodeError, ValueError):
        return message
    if (
        isinstance(parsed, dict)
        and "detail" in parsed
        and isinstance(parsed["detail"], str)
    ):
        return parsed["detail"]
    return message
