from __future__ import annotations

from eidolon_data.settings import default_sqlite_path
from eidolon_admin_server.app.ports import apply_ports_to_environ
from eidolon_admin_server.app.settings import Settings, default_voiceprint_root


def test_ports_export_eidolon_data_sqlite_path(monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_DATA_SQLITE_PATH", raising=False)
    exported = apply_ports_to_environ()
    assert exported["EIDOLON_DATA_SQLITE_PATH"] == str(default_sqlite_path())
    assert "EIDOLON_REGISTRY_DB_PATH" not in exported


def test_settings_no_longer_exposes_registry_db_path() -> None:
    assert not hasattr(Settings(), "registry_db_path")


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
