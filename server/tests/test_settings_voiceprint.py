from __future__ import annotations

from eidolon_admin_server.app.settings import (
    Settings,
    default_registry_db_path,
    default_voiceprint_root,
)


def test_default_registry_db_path_uses_eidolon_home(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_ADMIN_REGISTRY_DB_PATH", raising=False)
    monkeypatch.delenv("EIDOLON_REGISTRY_DB_PATH", raising=False)
    db_path = default_registry_db_path()
    assert db_path.name == "registry.sqlite3"
    assert db_path.parent.name == "db"
    assert db_path.parent.parent.name == "eidolon"


def test_default_registry_db_path_honors_shared_env(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("EIDOLON_ADMIN_REGISTRY_DB_PATH", raising=False)
    target = tmp_path / "registry.sqlite3"
    monkeypatch.setenv("EIDOLON_REGISTRY_DB_PATH", str(target))
    assert default_registry_db_path() == target
    assert Settings().registry_db_path == target


def test_default_voiceprint_root_uses_eidolon_home(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_VOICEPRINT_ROOT", raising=False)
    root = default_voiceprint_root()
    assert root.name == "voiceprints"
    assert root.parent.name == "eidolon"


def test_default_voiceprint_root_honors_shared_env(monkeypatch, tmp_path) -> None:
    target = tmp_path / "voiceprints"
    monkeypatch.setenv("EIDOLON_VOICEPRINT_ROOT", str(target))
    assert default_voiceprint_root() == target
    assert Settings().voiceprint_root == target
