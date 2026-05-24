"""REST surface for the Configs module.

  GET    /api/configs                                  list every declared file
  GET    /api/configs/{svc}/{cfg}                      raw text + parsed (masked) view
  PUT    /api/configs/{svc}/{cfg}                      write with backup + validation
  POST   /api/configs/{svc}/{cfg}/reload                trigger declared reload action
  GET    /api/configs/{svc}/{cfg}/backups              list snapshots
  POST   /api/configs/{svc}/{cfg}/restore?ts=<int>     restore from a snapshot

Path safety:
- Only files explicitly declared in services.yaml are reachable; the registry
  resolves absolute paths server-side. The {svc}/{cfg} URL is purely a lookup
  key — there is NO filesystem traversal from user input.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import backups, reload as reload_module
from .formats import ConfigFormatError, parsed_view, validate
from .registry import ResolvedConfig, by_service, find

router = APIRouter(prefix="/configs", tags=["configs"])


def _registry_lookup(request: Request, svc: str, cfg: str) -> ResolvedConfig:
    gw_cfg = request.app.state.gateway_config
    entry = find(gw_cfg, svc, cfg)
    if entry is None:
        raise HTTPException(404, f"unknown config: {svc}/{cfg}")
    return entry


def _entry_payload(e: ResolvedConfig) -> dict:
    return {
        "service_id": e.service_id,
        "config_id": e.config_id,
        "label": e.label,
        "path": str(e.path),
        "format": e.format,
        "reload": e.reload,
        "reload_target": e.reload_target,
        "template": str(e.template) if e.template else None,
        "template_exists": bool(e.template and e.template.exists()),
        "exists": e.exists,
    }


# ---- listing ----------------------------------------------------------------


@router.get("")
async def list_configs(request: Request) -> dict:
    grouped = by_service(request.app.state.gateway_config)
    return {
        "services": [
            {
                "service_id": svc_id,
                "configs": [_entry_payload(e) for e in entries],
            }
            for svc_id, entries in grouped.items()
        ]
    }


# ---- read -------------------------------------------------------------------


@router.get("/{svc}/{cfg}")
async def read_config(svc: str, cfg: str, request: Request) -> dict:
    entry = _registry_lookup(request, svc, cfg)
    if not entry.exists:
        # Offer to seed from template (informational; client can POST to /restore-template).
        return {
            **_entry_payload(entry),
            "text": "",
            "parsed": None,
            "missing": True,
            "mtime": None,
        }
    text = entry.path.read_text(encoding="utf-8")
    try:
        parsed = parsed_view(text, entry.format)
        parse_error = None
    except ConfigFormatError as exc:
        parsed = None
        parse_error = str(exc)
    return {
        **_entry_payload(entry),
        "text": text,
        "parsed": parsed,
        "parse_error": parse_error,
        "mtime": entry.path.stat().st_mtime,
    }


# ---- write ------------------------------------------------------------------


class _WriteBody(BaseModel):
    text: str = Field(..., min_length=0)


@router.put("/{svc}/{cfg}")
async def write_config(svc: str, cfg: str, body: _WriteBody, request: Request) -> dict:
    entry = _registry_lookup(request, svc, cfg)
    # Validate before touching disk.
    try:
        validate(body.text, entry.format)
    except ConfigFormatError as exc:
        raise HTTPException(400, str(exc)) from exc

    snap = backups.snapshot(entry.path)
    _atomic_write(entry.path, body.text)
    return {
        **_entry_payload(entry),
        "mtime": entry.path.stat().st_mtime,
        "backup": (
            {"timestamp": snap.timestamp, "size": snap.size, "path": str(snap.path)}
            if snap
            else None
        ),
    }


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cfg-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---- reload -----------------------------------------------------------------


@router.post("/{svc}/{cfg}/reload")
async def reload_config(svc: str, cfg: str, request: Request) -> dict:
    entry = _registry_lookup(request, svc, cfg)
    sv = request.app.state.supervisor_client
    return await reload_module.trigger(sv, entry.reload, entry.reload_target)


# ---- backups ----------------------------------------------------------------


@router.get("/{svc}/{cfg}/backups")
async def list_backups(svc: str, cfg: str, request: Request) -> dict:
    entry = _registry_lookup(request, svc, cfg)
    snapshots = backups.list_backups(entry.path)
    return {
        "backups": [
            {"timestamp": b.timestamp, "size": b.size, "path": str(b.path)}
            for b in snapshots
        ]
    }


@router.post("/{svc}/{cfg}/restore")
async def restore_config(
    svc: str,
    cfg: str,
    request: Request,
    ts: int = Query(..., description="unix timestamp of the backup to restore"),
) -> dict:
    entry = _registry_lookup(request, svc, cfg)
    try:
        backups.restore(entry.path, ts)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return {**_entry_payload(entry), "restored_from": ts}
