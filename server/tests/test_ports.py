from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from eidolon_admin_server.app.ports import (
    _MAX_LIST_INDEX,
    PortsRegistryError,
    _set_nested,
    _sync_yaml,
    apply_ports_to_environ,
    collect_ports_from_subprojects,
    collect_ports_registry,
    load_ports,
    ports_file,
)
from eidolon_admin_server.app.settings import Settings, load_gateway_config


def test_apply_ports_exports_authority_and_directory_ports(monkeypatch) -> None:
    for name in (
        "EIDOLON_HUB_API_PORT",
        "EIDOLON_DATA_API_PORT",
        "EIDOLON_DATA_WORKSPACE_API_PORT",
        "EIDOLON_KERNEL_API_PORT",
        "EIDOLON_SYSTEM_API_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    apply_ports_to_environ()
    assert os.environ.get("EIDOLON_HUB_API_PORT") == "8082"
    assert os.environ.get("EIDOLON_DATA_API_PORT") == "8084"
    assert os.environ.get("EIDOLON_DATA_WORKSPACE_API_PORT") == "8085"
    assert os.environ.get("EIDOLON_KERNEL_API_PORT") == "8083"
    assert os.environ.get("EIDOLON_SYSTEM_API_PORT") == "8090"


def test_apply_ports_does_not_export_foreign_database_paths(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_ADMIN_API_URL", raising=False)
    monkeypatch.delenv("EIDOLON_DATA_SQLITE_PATH", raising=False)
    monkeypatch.delenv("EIDOLON_REGISTRY_DB_PATH", raising=False)
    apply_ports_to_environ()
    assert os.environ.get("EIDOLON_ADMIN_API_URL") == "http://127.0.0.1:9000"
    assert os.environ.get("EIDOLON_DATA_SQLITE_PATH") is None
    assert os.environ.get("EIDOLON_REGISTRY_DB_PATH") is None


def test_gateway_config_uses_port_registry(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_HUB_API_PORT", raising=False)
    cfg = load_gateway_config()
    hub = cfg.find("hub")
    assert hub is not None
    assert hub.base_url == "http://127.0.0.1:8082"
    assert hub.ports.declared == [8082]
    assert cfg.find("data").integration == "infra"  # type: ignore[union-attr]
    assert cfg.find("data").base_url == ""  # type: ignore[union-attr]
    assert cfg.find("kernel").integration == "infra"  # type: ignore[union-attr]
    assert cfg.find("eidolond").integration == "infra"  # type: ignore[union-attr]


def test_blank_directory_uds_does_not_override_http_directory() -> None:
    assert Settings(system_directory_uds="").system_directory_uds is None


def test_ports_registry_has_expected_sections() -> None:
    ports = load_ports()
    assert ports["hub"]["api"]["port"] == 8082
    assert ports["data"]["api"]["port"] == 8084
    assert ports["data"]["workspace_api"]["port"] == 8085
    assert ports["kernel"]["api"]["port"] == 8083
    assert ports["eidolond"]["api"]["port"] == 8090
    assert ports["livekit"]["port"] == 7880
    assert ports["client_web"]["port"] == 3001
    assert ports["memory"]["mcp"]["port"] == 10030


def test_collect_ports_from_agent_settings(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    agent_dir = root / "eidolon_agent/config"
    agent_dir.mkdir(parents=True)
    agent_dir.joinpath("settings.yaml").write_text(
        yaml.safe_dump(
            {
                "http": {"port": 9191, "admin_port": 9192},
                "grpc": {"tcp_port": 46000},
                "nats": {"url": "nats://127.0.0.1:4333"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    # Minimal stubs so collect does not fall back to missing files only.
    for repo, body in (
        (
            "eidolon_hub",
            {
                "api": {"host": "0.0.0.0", "port": 8082},
                "livekit": {"api_url": "http://127.0.0.1:7880"},
            },
        ),
        (
            "eidolon_memory",
            {
                "discovery_http": {"host": "127.0.0.1", "port": 8020},
                "mcp_http": {"port": 8030},
            },
        ),
        ("eidolon_channel", {"core": {"port": 8766}}),
    ):
        d = root / repo / "config"
        d.mkdir(parents=True)
        d.joinpath("settings.yaml").write_text(
            yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
        )

    monkeypatch.setattr(
        "eidolon_admin_server.app.settings.default_eidolon_root",
        lambda: root,
    )
    ports = collect_ports_from_subprojects(root)
    assert ports["agent"]["http"]["port"] == 9191
    assert ports["agent"]["admin"]["port"] == 9192
    assert ports["agent"]["grpc"]["port"] == 46000
    assert ports["nats"]["port"] == 4333
    assert ports["data"]["api"]["port"] == 8084
    assert ports["data"]["workspace_api"]["port"] == 8085
    assert ports["kernel"]["api"]["port"] == 8083
    assert ports["eidolond"]["api"]["port"] == 8090


def test_collect_ports_registry_writes_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    agent_dir = root / "eidolon_agent/config"
    agent_dir.mkdir(parents=True)
    agent_dir.joinpath("settings.yaml").write_text(
        yaml.safe_dump(
            {"http": {"port": 7777, "admin_port": 7778}, "grpc": {"tcp_port": 45051}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for repo, body in (
        (
            "eidolon_hub",
            {"api": {"port": 8082}, "livekit": {"api_url": "http://127.0.0.1:7880"}},
        ),
        (
            "eidolon_memory",
            {"discovery_http": {"port": 8020}, "mcp_http": {"port": 8030}},
        ),
        ("eidolon_channel", {"core": {"port": 8766}}),
    ):
        d = root / repo / "config"
        d.mkdir(parents=True)
        d.joinpath("settings.yaml").write_text(
            yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
        )

    ports_path = tmp_path / "admin" / "config" / "ports.yaml"
    ports_path.parent.mkdir(parents=True)
    ports_path.write_text(
        yaml.safe_dump(
            {
                "admin": {
                    "api": {"host": "127.0.0.1", "port": 9000},
                    "web": {"port": 9001},
                }
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(
        "eidolon_admin_server.app.settings.default_eidolon_root", lambda: root
    )
    monkeypatch.setattr("eidolon_admin_server.app.ports.ports_file", lambda: ports_path)

    out = collect_ports_registry(root)
    assert out == ports_path
    data = yaml.safe_load(ports_path.read_text(encoding="utf-8"))
    assert data["agent"]["http"]["port"] == 7777


def test_sync_yaml_updates_list_leaf_without_clobbering_models(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "models": [
                        {
                            "name": "openai/deepseek-v4-flash",
                            "api_base": "http://127.0.0.1:9999/v1",
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


def test_sync_yaml_recovers_models_dict_corruption(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"llm": {"models": {}, "default_model": "x"}}, sort_keys=False),
        encoding="utf-8",
    )
    assert _sync_yaml(path, {"llm.models.0.api_base": "http://127.0.0.1:8180/v1"})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data["llm"]["models"], list)


def test_apply_ports_overrides_empty_env_value(monkeypatch) -> None:
    monkeypatch.setenv("EIDOLON_ADMIN_API_PORT", "")
    apply_ports_to_environ()
    assert os.environ["EIDOLON_ADMIN_API_PORT"] != ""
    assert os.environ["EIDOLON_ADMIN_API_PORT"].strip() != ""


def test_apply_ports_preserves_explicit_env_override(monkeypatch) -> None:
    monkeypatch.setenv("EIDOLON_ADMIN_API_PORT", "65000")
    apply_ports_to_environ()
    assert os.environ["EIDOLON_ADMIN_API_PORT"] == "65000"


def test_set_nested_rejects_oversized_list_index() -> None:
    data: dict[str, object] = {"llm": {"models": []}}
    assert _set_nested(data, f"llm.models.{_MAX_LIST_INDEX + 1}.api_base", "x") is False
    assert data == {"llm": {"models": []}}


def test_set_nested_allows_index_at_cap() -> None:
    data: dict[str, object] = {"llm": {"models": []}}
    assert _set_nested(data, f"llm.models.{_MAX_LIST_INDEX}.api_base", "ok") is True
    models = data["llm"]["models"]  # type: ignore[index]
    assert isinstance(models, list)
    assert len(models) == _MAX_LIST_INDEX + 1
    assert models[_MAX_LIST_INDEX] == {"api_base": "ok"}


def test_the_registry_admin_falls_back_to_is_its_own(monkeypatch) -> None:
    """Nothing here may depend on a sibling checkout's layout.

    A deployed Host has no ``eidolon_ops`` working tree to read, so the
    development fallback has to be a file this repository owns. Pointing
    EIDOLON_OPS_ROOT at somewhere that does not exist must change nothing.
    """
    monkeypatch.delenv("EIDOLON_PORTS_FILE", raising=False)
    monkeypatch.setenv("EIDOLON_OPS_ROOT", "/nonexistent/eidolon_ops")

    target = ports_file()
    admin_root = Path(__file__).resolve().parents[2]
    assert target == admin_root / "config" / "ports.yaml"
    assert load_ports()["admin"]["api"]["port"] == 9000


def test_a_host_that_was_told_where_its_registry_is_reads_that_one(
    tmp_path: Path, monkeypatch
) -> None:
    """How every deployed Host learns its ports: Ops names the file."""
    registry = tmp_path / "generated" / "ports.yaml"
    registry.parent.mkdir()
    registry.write_text(
        yaml.safe_dump({"admin": {"api": {"host": "127.0.0.1", "port": 9500}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EIDOLON_PORTS_FILE", str(registry))

    assert ports_file() == registry.resolve()
    assert load_ports()["admin"]["api"]["port"] == 9500


def test_an_absent_registry_says_which_file_and_who_named_it(
    tmp_path: Path, monkeypatch
) -> None:
    missing = tmp_path / "generated" / "ports.yaml"
    monkeypatch.setenv("EIDOLON_PORTS_FILE", str(missing))

    with pytest.raises(PortsRegistryError) as error:
        load_ports()

    message = str(error.value)
    assert str(missing) in message
    assert "EIDOLON_PORTS_FILE" in message


def test_an_absent_default_registry_says_nothing_named_one(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("EIDOLON_PORTS_FILE", raising=False)
    monkeypatch.setattr(
        "eidolon_admin_server.app.ports._DEVELOPMENT_PORTS_FILE",
        tmp_path / "ports.yaml",
    )

    with pytest.raises(PortsRegistryError) as error:
        load_ports()

    assert "EIDOLON_PORTS_FILE is unset" in str(error.value)


def test_a_registry_short_of_a_section_names_the_section(monkeypatch) -> None:
    """The failure this replaces was a bare ``KeyError: 'admin'``.

    It named neither the file it came from nor that a file was the problem.
    """
    with pytest.raises(PortsRegistryError) as error:
        apply_ports_to_environ({"hub": {"api": {"host": "127.0.0.1", "port": 8082}}})

    assert "'admin'" in str(error.value)


def test_collect_still_runs_before_any_registry_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """``collect`` writes the registry, so it is the one caller allowed to
    start without one."""
    monkeypatch.setenv("EIDOLON_PORTS_FILE", str(tmp_path / "ports.yaml"))
    monkeypatch.setattr(
        "eidolon_admin_server.app.settings.default_eidolon_root", lambda: tmp_path
    )

    ports = collect_ports_from_subprojects(tmp_path)

    assert ports["admin"]["api"]["port"] == 9000
    assert ports["livekit"]["rtc_port_end"] == 60000
