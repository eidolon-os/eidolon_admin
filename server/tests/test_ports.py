from __future__ import annotations

import os

from eidolon_admin_server.app.ports import apply_ports_to_environ, load_ports
from eidolon_admin_server.app.settings import load_gateway_config


def test_apply_ports_exports_hub_port(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_HUB_API_PORT", raising=False)
    apply_ports_to_environ()
    assert os.environ.get("EIDOLON_HUB_API_PORT") == "8082"


def test_gateway_config_uses_port_registry(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_HUB_API_PORT", raising=False)
    cfg = load_gateway_config()
    hub = cfg.find("hub")
    assert hub is not None
    assert hub.base_url == "http://127.0.0.1:8082"
    assert hub.ports.declared == [8082]


def test_ports_registry_has_expected_sections() -> None:
    ports = load_ports()
    assert ports["hub"]["api"]["port"] == 8082
    assert ports["livekit"]["port"] == 7880
