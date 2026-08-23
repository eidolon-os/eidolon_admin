from __future__ import annotations

import subprocess
import sys


def test_lifecycle_workflow_entrypoint_imports_in_a_fresh_interpreter() -> None:
    """The systemd process must not depend on Admin having warmed imports."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from eidolon_admin_server.lifecycle_workflow.daemon import main; "
                "assert callable(main)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
