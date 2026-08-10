"""Detached, shell-free Admin self-restart helper for the Ops executor."""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path


_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supervisorctl", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    arguments = parser.parse_args(argv)
    if _TARGET.fullmatch(arguments.target) is None:
        parser.error("target is not a safe Supervisor process name")
    if not 0 <= arguments.delay_seconds <= 5:
        parser.error("delay-seconds must be between 0 and 5")
    if not arguments.supervisorctl.is_file() or not arguments.config.is_file():
        return 1
    time.sleep(arguments.delay_seconds)
    result = subprocess.run(
        (
            str(arguments.supervisorctl),
            "-c",
            str(arguments.config),
            "restart",
            arguments.target,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
