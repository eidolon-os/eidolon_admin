"""Admin reaches the Channel Provider with the management role and no other.

Every Admin call to the Provider — presence, session traces, voiceprints,
voices, device settings, Mission Control, and the generic service proxy — takes
its credential from the registry entry through ``upstream_auth_headers``. So
the entry is the one place that decides which role Admin presents, and this
pins it to the management role. Admin used to present Hub's credential there,
which the Provider also accepted for provisioning and revoking channels.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers
from eidolon_admin_server.app.settings import GatewayConfig

_ROOT = Path(__file__).resolve().parents[2]
_MANAGEMENT = "EIDOLON_CHANNEL_PROVIDER_MANAGEMENT_TOKEN"
#: Hub's role at the Provider, and the single credential every caller shared
#: before roles. Admin must hold neither.
_NOT_ADMINS = ("EIDOLON_CHANNEL_PROVIDER_HUB_TOKEN", "EIDOLON_CHANNEL_PROVIDER_TOKEN")


def _provider_entry(monkeypatch):
    monkeypatch.setenv("EIDOLON_CHANNEL_PROVIDER_PORT", "8767")
    text = (_ROOT / "config/services.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(os.path.expandvars(text))
    raw["services"] = [s for s in raw["services"] if s["id"] == "channel-provider"]
    (entry,) = GatewayConfig.model_validate(raw).services
    return entry


def test_the_registry_presents_the_management_role(monkeypatch) -> None:
    entry = _provider_entry(monkeypatch)
    assert entry.auth.type == "bearer"
    assert entry.auth.token_env == _MANAGEMENT

    monkeypatch.setenv(_MANAGEMENT, "management-credential")
    # Present on a Host installed before roles; read by nothing here.
    for name in _NOT_ADMINS:
        monkeypatch.setenv(name, "not-admins")
    assert upstream_auth_headers(entry) == {"Authorization": "Bearer management-credential"}


def test_no_admin_code_reads_another_roles_provider_credential() -> None:
    sources = [
        *(_ROOT / "server/eidolon_admin_server").rglob("*.py"),
        *(_ROOT / "config").glob("*.yaml"),
    ]
    readers = sorted(
        str(path.relative_to(_ROOT))
        for path in sources
        if any(name in path.read_text(encoding="utf-8") for name in _NOT_ADMINS)
    )
    assert readers == []
