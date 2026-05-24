"""Read-only view of a sub-project's dotenv file.

Used by 'process' integration services (channel, client-web, etc.) to surface
their config in the admin UI without modifying the sub-project. The parser is
intentionally strict (KEY=VALUE only, never `source`) so weirdly-quoted values
that would crash bash never execute.

Secrets (keys matching SECRET_HINTS) are masked before returning to the UI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_SECRET_HINTS = re.compile(
    r"(secret|key|token|password|passwd|pwd|api_key)",
    re.IGNORECASE,
)


def _parse_dotenv(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out.append((key, val))
    return out


def _mask(key: str, value: str) -> str:
    if not value or not _SECRET_HINTS.search(key):
        return value
    if len(value) <= 6:
        return "***"
    return f"{value[:2]}…{value[-2:]} ({len(value)} chars)"


def read_dotenv_view(path: Path, *, missing_hint: str = "") -> dict[str, Any]:
    """Return a JSON-serialisable view of ``path``.

    Raises HTTPException(404) if the file doesn't exist. Secrets are masked.
    """
    if not path.exists():
        detail = f"env file not found: {path}"
        if missing_hint:
            detail += f" — {missing_hint}"
        raise HTTPException(404, detail)
    entries = _parse_dotenv(path)
    return {
        "env_file": str(path),
        "entries": [
            {"key": k, "value": _mask(k, v), "masked": bool(_SECRET_HINTS.search(k))}
            for k, v in entries
        ],
    }
