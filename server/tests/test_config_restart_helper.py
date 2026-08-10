from __future__ import annotations

import subprocess
from pathlib import Path

from eidolon_admin_server.app.configs import reload, restart_helper


def test_restart_helper_invokes_supervisorctl_without_a_shell(
    tmp_path: Path, monkeypatch
) -> None:
    supervisorctl = tmp_path / "supervisorctl"
    config = tmp_path / "supervisord.conf"
    supervisorctl.write_text("executable", encoding="utf-8")
    config.write_text("config", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def run(command, **kwargs):
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(restart_helper.subprocess, "run", run)

    assert restart_helper.main(
        [
            "--supervisorctl",
            str(supervisorctl),
            "--config",
            str(config),
            "--target",
            "admin:admin-api",
            "--delay-seconds",
            "0",
        ]
    ) == 0
    assert calls == [
        (
            str(supervisorctl),
            "-c",
            str(config),
            "restart",
            "admin:admin-api",
        )
    ]


def test_self_restart_uses_ops_python_module(tmp_path: Path, monkeypatch) -> None:
    python = tmp_path / "python"
    supervisorctl = tmp_path / "supervisorctl"
    config = tmp_path / "supervisord.conf"
    for path in (python, supervisorctl, config):
        path.write_text("present", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(reload, "_OPS_PYTHON", python)
    monkeypatch.setattr(reload, "_SUPERVISORCTL", supervisorctl)
    monkeypatch.setattr(reload, "_SUPERVISORD_CONF", config)
    monkeypatch.setattr(
        reload.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)),
    )

    result = reload._self_restart("admin:admin-api")

    assert result["self_restart"] is True
    assert calls[0][0] == str(python)
    assert calls[0][1:3] == ["-m", "eidolon_admin_server.app.configs.restart_helper"]
    assert "/bin/sh" not in calls[0]
