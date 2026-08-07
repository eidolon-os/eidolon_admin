"""Format-specific parse / validate helpers.

Each config file declares its `format` (yaml / dotenv / ini); this module
provides a uniform interface so the router doesn't care about specifics.

Validation = "can this text be parsed without raising"; we don't enforce
project-specific schemas (that's the project's own loader's job at boot time).
"""

from __future__ import annotations

import configparser
import re
from typing import Any

import yaml


class ConfigFormatError(ValueError):
    """Raised when text fails to parse. Maps to HTTP 400 at the router."""


def parse_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigFormatError(f"YAML parse error: {exc}") from exc


def parse_dotenv(text: str) -> list[tuple[str, str]]:
    """Lenient dotenv parser. Returns ordered KEY=VALUE pairs.

    Ignores comments and blank lines; surfaces lines that look like assignments
    but aren't well-formed (e.g. missing `=`). Same logic as our supervisord
    `with-env.sh` wrapper, so what we accept here is what the runtime accepts.
    """
    out: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not m:
            raise ConfigFormatError(f"line {lineno}: not a valid KEY=VALUE: {raw!r}")
        key = m.group(1)
        val = m.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out.append((key, val))
    return out


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigFormatError(f"INI parse error: {exc}") from exc
    return {sec: dict(parser[sec]) for sec in parser.sections()}


def validate(text: str, fmt: str) -> Any:
    """Try-parse `text` as `fmt`. Returns the parsed structure on success;
    raises ConfigFormatError on failure."""
    if fmt == "yaml":
        return parse_yaml(text)
    if fmt == "dotenv":
        return parse_dotenv(text)
    if fmt == "ini":
        return parse_ini(text)
    raise ConfigFormatError(f"unsupported format: {fmt}")


# -- secret masking (for parsed views; raw editor sees real text) -------------

_SECRET_HINTS = re.compile(
    r"(secret|key|token|password|passwd|pwd|api_key)",
    re.IGNORECASE,
)


def _mask_value(key: str, value: str) -> str:
    if not value or not _SECRET_HINTS.search(key):
        return value
    if len(value) <= 6:
        return "***"
    return f"{value[:2]}…{value[-2:]} ({len(value)} chars)"


def parsed_view(text: str, fmt: str) -> dict[str, Any]:
    """Return a structured view of the config with secrets masked.

    Frontend renders this side-by-side with the raw editor so users can spot
    structural mistakes without scanning the whole file.
    """
    raw = validate(text, fmt)
    if fmt == "dotenv":
        return {
            "entries": [
                {
                    "key": k,
                    "value": _mask_value(k, v),
                    "masked": bool(_SECRET_HINTS.search(k)),
                }
                for k, v in raw
            ],
        }
    if fmt == "yaml":
        return {"data": _mask_nested(raw)}
    if fmt == "ini":
        return {
            "sections": {
                s: {k: _mask_value(k, v) for k, v in kv.items()}
                for s, kv in raw.items()
            }
        }
    return {"data": raw}


def _mask_nested(node: Any) -> Any:
    """Recursively mask values whose key matches secret hints in nested
    yaml structures."""
    if isinstance(node, dict):
        return {
            k: (_mask_value(k, str(v)) if _looks_like_secret(k, v) else _mask_nested(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_mask_nested(x) for x in node]
    return node


def _looks_like_secret(key: Any, value: Any) -> bool:
    if not isinstance(key, str) or not _SECRET_HINTS.search(key):
        return False
    return isinstance(value, (str, int, float))
