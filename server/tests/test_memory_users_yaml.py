"""Tests for atomic users.yaml read/write."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from eidolon_admin_server.app.memory.runners import ConsolidatorConfig, UserEntry
from eidolon_admin_server.app.memory.users_yaml import (
    UsersYamlError,
    get_user,
    read_users,
    set_consolidator,
    set_enabled,
    upsert_user,
)


def _seed(path: Path) -> None:
    path.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: ''
          - id: bob
            port: 8031
            enabled: false
            palace_path: '/tmp/bob'
    """))


def test_read_users(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    users = read_users(p)
    assert [u.id for u in users] == ["alice", "bob"]
    assert users[1].palace_path == "/tmp/bob"


def test_upsert_replaces_existing(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    res = upsert_user(
        UserEntry(id="alice", port=8030, enabled=False, palace_path="/x"),
        path=p,
    )
    after = read_users(p)
    assert next(u for u in after if u.id == "alice").enabled is False
    assert next(u for u in after if u.id == "alice").palace_path == "/x"
    # Order preserved (alice still first).
    assert [u.id for u in after] == ["alice", "bob"]
    assert res.path == p


def test_upsert_appends_new(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    upsert_user(UserEntry(id="carol", port=8032, enabled=True), path=p)
    ids = [u.id for u in read_users(p)]
    assert ids == ["alice", "bob", "carol"]


def test_upsert_rejects_duplicate_port(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    with pytest.raises(UsersYamlError, match="port 8030 reused"):
        upsert_user(UserEntry(id="dave", port=8030, enabled=True), path=p)


def test_upsert_rejects_invalid_port(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    with pytest.raises(UsersYamlError, match="invalid port"):
        upsert_user(UserEntry(id="dave", port=0, enabled=True), path=p)


def test_set_enabled(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    set_enabled("bob", True, path=p)
    assert get_user("bob", path=p).enabled is True
    set_enabled("bob", False, path=p)
    assert get_user("bob", path=p).enabled is False


def test_set_enabled_unknown_user(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    with pytest.raises(UsersYamlError, match="unknown user"):
        set_enabled("ghost", True, path=p)


def test_atomic_write_no_partial_file(tmp_path, monkeypatch):
    """If serialization succeeds, the file is fully present; no .tmp leaks."""
    p = tmp_path / "users.yaml"
    _seed(p)
    upsert_user(UserEntry(id="ed", port=8099, enabled=True), path=p)
    # No leftover temp files.
    leftover = list(tmp_path.glob(".users-*"))
    assert leftover == []
    # File is parseable.
    after = read_users(p)
    assert any(u.id == "ed" for u in after)


def test_read_missing_file_returns_empty(tmp_path):
    p = tmp_path / "nope.yaml"
    assert read_users(p) == []


def test_upsert_creates_file(tmp_path):
    p = tmp_path / "new.yaml"
    upsert_user(UserEntry(id="alpha", port=8030, enabled=True), path=p)
    assert p.exists()
    assert read_users(p)[0].id == "alpha"


def test_read_preserves_consolidator_block(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: ''
            consolidator:
              enabled: true
              interval_hours: 4
              window_days: 14
              min_drawers: 2
              min_confidence: 0.7
    """))
    users = read_users(p)
    assert users[0].consolidator_enabled() is True
    assert users[0].consolidator is not None
    assert users[0].consolidator.interval_hours == 4


def test_upsert_without_consolidator_preserves_existing_block(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            consolidator:
              enabled: true
              interval_hours: 6
    """))
    upsert_user(
        UserEntry(id="alice", port=8030, enabled=False, palace_path="/x"),
        path=p,
    )
    alice = get_user("alice", path=p)
    assert alice is not None
    assert alice.enabled is False
    assert alice.consolidator is not None
    assert alice.consolidator.enabled is True


def test_set_consolidator_and_remove(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    set_consolidator(
        "bob",
        ConsolidatorConfig(enabled=True, interval_hours=3, window_days=7),
        path=p,
    )
    bob = get_user("bob", path=p)
    assert bob.consolidator_enabled() is True
    assert bob.consolidator.interval_hours == 3

    set_consolidator("bob", None, path=p)
    bob2 = get_user("bob", path=p)
    assert bob2.consolidator is None


def test_set_consolidator_rejects_invalid_interval(tmp_path):
    p = tmp_path / "users.yaml"
    _seed(p)
    with pytest.raises(UsersYamlError, match="interval_hours"):
        set_consolidator(
            "alice",
            ConsolidatorConfig(enabled=True, interval_hours=0),
            path=p,
        )
