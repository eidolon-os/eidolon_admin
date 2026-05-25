from __future__ import annotations

import os
from pathlib import Path

import yaml

from eidolon_admin_server.app.ports import (
    _sync_yaml,
    apply_ports_to_environ,
    load_ports,
)
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


def test_sync_yaml_updates_list_leaf_without_clobbering_models(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "models": [
                        {
                            "name": "openai/deepseek-v4-flash",
                            "api_base": "http://127.0.0.1:8080/v1",
                        }
                    ],
                    "default_model": "openai/deepseek-v4-flash",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert _sync_yaml(path, {"llm.models.0.api_base": "http://127.0.0.1:8180/v1"})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    models = data["llm"]["models"]
    assert isinstance(models, list)
    assert models[0]["api_base"] == "http://127.0.0.1:8180/v1"
    assert models[0]["name"] == "openai/deepseek-v4-flash"


def test_sync_yaml_recovers_models_dict_corruption(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"llm": {"models": {}, "default_model": "x"}}, sort_keys=False),
        encoding="utf-8",
    )
    assert _sync_yaml(path, {"llm.models.0.api_base": "http://127.0.0.1:8180/v1"})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data["llm"]["models"], list)
    assert data["llm"]["models"][0]["api_base"] == "http://127.0.0.1:8180/v1"
