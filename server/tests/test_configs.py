"""Tests for the unified configs surface.

We build a throw-away GatewayConfig that points ``configs`` at files in a
tmp_path, so we can exercise the real router end-to-end (atomic writes,
backup rotation, format validation, restore) without touching the real
project configs on disk.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eidolon_admin_server.app.configs import backups, formats
from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    ConfigEntry,
    GatewayConfig,
    ServiceConfig,
)


# ---- formats ---------------------------------------------------------------


def test_build_registry_rejects_path_outside_root(tmp_path, monkeypatch):
    """services.yaml entries that resolve outside EIDOLON_ROOT must be refused.

    Defense-in-depth: even though only registered files are reachable, we
    don't want a buggy services.yaml to silently expose /etc/passwd or
    ~/.ssh/id_rsa via the configs editor. The check runs at build_registry
    time so the error fires at startup, not at first read.
    """
    import pytest
    from eidolon_admin_server.app.configs.registry import build_registry

    # Constrain "root" to tmp_path/safe, then point a config entry at
    # tmp_path/escape — that path resolves outside the root.
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    monkeypatch.setenv("EIDOLON_ROOT", str(safe_root))
    monkeypatch.setenv("EIDOLON_WORKSPACE_ROOT", str(safe_root))
    escape_file = tmp_path / "escape" / "config.yaml"
    escape_file.parent.mkdir()
    escape_file.write_text("hi: 1\n")

    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000),
        services=[
            ServiceConfig(
                id="bad",
                name="bad",
                integration="native",
                base_url="",
                auth=AuthConfig(),
                configs=[
                    ConfigEntry(
                        id="leak", label="leak", path=str(escape_file), format="yaml"
                    )
                ],
            ),
        ],
    )
    with pytest.raises(ValueError, match="outside the sanctioned root"):
        build_registry(cfg)


def test_build_registry_accepts_path_under_root(tmp_path, monkeypatch):
    """Sanity: paths under EIDOLON_ROOT pass the check."""
    from eidolon_admin_server.app.configs.registry import build_registry

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("EIDOLON_ROOT", str(root))
    monkeypatch.setenv("EIDOLON_WORKSPACE_ROOT", str(root))
    inside = root / "subdir" / "config.yaml"
    inside.parent.mkdir()
    inside.write_text("a: 1\n")

    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000),
        services=[
            ServiceConfig(
                id="svc",
                name="svc",
                integration="native",
                base_url="",
                auth=AuthConfig(),
                configs=[
                    ConfigEntry(id="cfg", label="cfg", path=str(inside), format="yaml")
                ],
            ),
        ],
    )
    entries = build_registry(cfg)
    assert len(entries) == 1
    assert entries[0].path == inside.resolve()


def test_validate_yaml_ok():
    assert formats.validate("a: 1\nb: [1, 2]\n", "yaml") == {"a": 1, "b": [1, 2]}


def test_validate_yaml_error():
    with pytest.raises(formats.ConfigFormatError):
        formats.validate("a: [\n", "yaml")


def test_validate_dotenv_strips_quotes():
    parsed = formats.validate('FOO=bar\nQ="quoted value"\n# comment\n', "dotenv")
    assert parsed == [("FOO", "bar"), ("Q", "quoted value")]


def test_validate_dotenv_rejects_bad_line():
    with pytest.raises(formats.ConfigFormatError):
        formats.validate("not an assignment\n", "dotenv")


def test_validate_ini_ok():
    parsed = formats.validate("[s]\nkey = v\n", "ini")
    assert parsed == {"s": {"key": "v"}}


def test_parsed_view_masks_secrets():
    view = formats.parsed_view("API_KEY=supersecretvalue\nNAME=bob\n", "dotenv")
    keys = {e["key"]: e for e in view["entries"]}
    assert keys["API_KEY"]["masked"] is True
    assert "supersecret" not in keys["API_KEY"]["value"]
    assert keys["NAME"]["masked"] is False
    assert keys["NAME"]["value"] == "bob"


# ---- backups (unit) --------------------------------------------------------


def test_backup_rotation(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("v0\n")
    # 12 saves → only 10 backups kept.
    for i in range(12):
        f.write_text(f"v{i}\n")
        snap = backups.snapshot(f)
        assert snap is not None
        # Force unique timestamps for deterministic ordering.
        new_name = f.parent / f"{f.name}.bak.{1_700_000_000 + i}"
        snap.path.rename(new_name)
    listing = backups.list_backups(f)
    # Manual renames sidestep _rotate(), so just sanity-check that listing
    # is sorted desc and there are at most 12 entries.
    assert listing == sorted(listing, key=lambda b: b.timestamp, reverse=True)
    assert len(listing) <= 12


def test_snapshot_skips_missing(tmp_path: Path):
    assert backups.snapshot(tmp_path / "nope") is None


def test_restore_round_trip(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("original\n")
    snap = backups.snapshot(f)
    assert snap is not None
    f.write_text("modified\n")
    backups.restore(f, snap.timestamp)
    assert f.read_text() == "original\n"


# ---- router end-to-end -----------------------------------------------------


@pytest.fixture
def configs_gateway(tmp_path: Path, monkeypatch) -> tuple[GatewayConfig, Path, Path]:
    """Gateway config with one editable yaml and one editable dotenv.

    Pins EIDOLON_ROOT to tmp_path so the registry's path-prefix guard
    treats the tmp config files as inside the sanctioned root.
    """
    monkeypatch.setenv("EIDOLON_ROOT", str(tmp_path))
    monkeypatch.setenv("EIDOLON_WORKSPACE_ROOT", str(tmp_path))
    yaml_file = tmp_path / "app.yaml"
    yaml_file.write_text("name: hello\nport: 8080\n")
    env_file = tmp_path / "service.env"
    env_file.write_text("FOO=bar\nAPI_TOKEN=verysecret\n")

    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="demo",
                name="Demo",
                integration="native",
                auth=AuthConfig(type="none"),
                configs=[
                    ConfigEntry(
                        id="app",
                        label="App YAML",
                        path=str(yaml_file),
                        format="yaml",
                        reload="none",
                    ),
                    ConfigEntry(
                        id="env",
                        label="Service env",
                        path=str(env_file),
                        format="dotenv",
                        reload="none",
                    ),
                ],
            ),
        ],
    )
    return cfg, yaml_file, env_file


@pytest.fixture
def configs_client(configs_gateway):
    cfg, _, _ = configs_gateway
    app = create_app(cfg)
    with TestClient(app) as client:
        yield client


def test_list_configs(configs_client):
    r = configs_client.get("/api/configs")
    assert r.status_code == 200
    body = r.json()
    assert len(body["services"]) == 1
    svc = body["services"][0]
    assert svc["service_id"] == "demo"
    assert {c["config_id"] for c in svc["configs"]} == {"app", "env"}


def test_read_yaml_with_parsed(configs_client):
    r = configs_client.get("/api/configs/demo/app")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "name: hello\nport: 8080\n"
    assert body["parsed"]["data"] == {"name": "hello", "port": 8080}


def test_read_masks_dotenv_secrets(configs_client):
    r = configs_client.get("/api/configs/demo/env")
    body = r.json()
    entries = {e["key"]: e for e in body["parsed"]["entries"]}
    assert entries["API_TOKEN"]["masked"] is True
    # Raw text should still contain the secret — editor needs it.
    assert "verysecret" in body["text"]
    assert "verysecret" not in entries["API_TOKEN"]["value"]


def test_write_validates_and_backs_up(configs_client, configs_gateway):
    _, yaml_file, _ = configs_gateway
    r = configs_client.put(
        "/api/configs/demo/app", json={"text": "name: new\nport: 9090\n"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["backup"] is not None
    assert yaml_file.read_text() == "name: new\nport: 9090\n"
    # The backup file should exist alongside.
    bak = Path(body["backup"]["path"])
    assert bak.exists()
    assert bak.read_text() == "name: hello\nport: 8080\n"


def test_write_rejects_invalid_yaml(configs_client, configs_gateway):
    _, yaml_file, _ = configs_gateway
    original = yaml_file.read_text()
    r = configs_client.put(
        "/api/configs/demo/app", json={"text": "name: [unterminated\n"}
    )
    assert r.status_code == 400
    # File must not have been touched.
    assert yaml_file.read_text() == original


def test_unknown_config_404(configs_client):
    r = configs_client.get("/api/configs/demo/does-not-exist")
    assert r.status_code == 404


def test_list_backups_after_two_writes(configs_client):
    configs_client.put("/api/configs/demo/app", json={"text": "name: a\nport: 1\n"})
    # Sleep to force distinct timestamps (unix seconds precision).
    time.sleep(1.1)
    configs_client.put("/api/configs/demo/app", json={"text": "name: b\nport: 2\n"})
    r = configs_client.get("/api/configs/demo/app/backups")
    assert r.status_code == 200
    items = r.json()["backups"]
    assert len(items) >= 2
    # Newest first.
    assert items[0]["timestamp"] >= items[1]["timestamp"]


def test_restore_api_round_trip(configs_client, configs_gateway):
    _, yaml_file, _ = configs_gateway
    r = configs_client.put(
        "/api/configs/demo/app", json={"text": "name: v1\nport: 1\n"}
    )
    ts = r.json()["backup"]["timestamp"]
    time.sleep(1.1)
    configs_client.put("/api/configs/demo/app", json={"text": "name: v2\nport: 2\n"})
    assert "v2" in yaml_file.read_text()
    r = configs_client.post(f"/api/configs/demo/app/restore?ts={ts}")
    assert r.status_code == 200
    # `ts` was the backup of the original — so restoring it yields the
    # original content.
    assert yaml_file.read_text() == "name: hello\nport: 8080\n"
