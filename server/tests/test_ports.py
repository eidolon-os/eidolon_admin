from __future__ import annotations

import os
from pathlib import Path

import yaml

from eidolon_admin_server.app.ports import (
    _MAX_LIST_INDEX,
    _set_nested,
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
    """Indexed update (``models.0.api_base``) must in-place edit the
    list element without replacing the whole list. Verifies regression
    fix for ``9327e6f`` — earlier behaviour rewrote ``models`` as a
    dict, breaking agent's config loader.

    Initial value differs from the sync target so the helper has actual
    work to do (``_sync_yaml`` returns False on a no-op).
    """
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "models": [
                        {
                            "name": "openai/deepseek-v4-flash",
                            "api_base": "http://127.0.0.1:9999/v1",  # initial != target
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
    assert isinstance(models, list)  # the regression: must remain a list
    assert models[0]["api_base"] == "http://127.0.0.1:8180/v1"
    assert models[0]["name"] == "openai/deepseek-v4-flash"  # sibling key untouched
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


def test_apply_ports_overrides_empty_env_value(monkeypatch) -> None:
    """An empty-string env var (e.g. ``export EIDOLON_ADMIN_API_PORT=""``)
    must be treated as "not set" so ports.yaml defaults take effect.

    Without this, ``os.path.expandvars`` later substitutes
    ``$EIDOLON_ADMIN_API_PORT`` to ``""`` inside services.yaml, producing
    broken URLs like ``http://127.0.0.1:/docs`` and silent probe failures.
    """
    from eidolon_admin_server.app.ports import apply_ports_to_environ

    monkeypatch.setenv("EIDOLON_ADMIN_API_PORT", "")  # operator's stale shell
    apply_ports_to_environ()
    assert os.environ["EIDOLON_ADMIN_API_PORT"] != ""
    assert os.environ["EIDOLON_ADMIN_API_PORT"].strip() != ""


def test_apply_ports_preserves_explicit_env_override(monkeypatch) -> None:
    """A non-empty env var WINS over ports.yaml defaults — that's the
    contract for operator overrides. Tested explicitly so future
    refactors of ``_env`` don't accidentally invert the precedence.
    """
    from eidolon_admin_server.app.ports import apply_ports_to_environ

    monkeypatch.setenv("EIDOLON_ADMIN_API_PORT", "65000")
    apply_ports_to_environ()
    assert os.environ["EIDOLON_ADMIN_API_PORT"] == "65000"


def test_set_nested_rejects_oversized_list_index() -> None:
    """A typo like ``llm.models.99999.api_base`` must not allocate 99999 dicts.

    Regression: _set_nested previously had no upper bound on list growth, so
    a single malformed dotted-path entry could force admin to allocate a
    huge list at startup and write a multi-MB yaml back out.
    """
    data: dict[str, object] = {"llm": {"models": []}}
    # Index just over the cap is rejected (returns False, leaves data alone).
    assert _set_nested(data, f"llm.models.{_MAX_LIST_INDEX + 1}.api_base", "x") is False
    assert data == {"llm": {"models": []}}


def test_set_nested_allows_index_at_cap() -> None:
    """The cap is inclusive — index == _MAX_LIST_INDEX must still succeed."""
    data: dict[str, object] = {"llm": {"models": []}}
    assert _set_nested(data, f"llm.models.{_MAX_LIST_INDEX}.api_base", "ok") is True
    assert isinstance(data["llm"], dict)
    models = data["llm"]["models"]  # type: ignore[index]
    assert isinstance(models, list)
    assert len(models) == _MAX_LIST_INDEX + 1
    assert models[_MAX_LIST_INDEX] == {"api_base": "ok"}
