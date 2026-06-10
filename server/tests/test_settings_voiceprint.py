from __future__ import annotations

from eidolon_admin_server.app.settings import Settings, default_voiceprint_root


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
