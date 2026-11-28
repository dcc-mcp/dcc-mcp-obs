"""Launch OBS Studio from its binary directory and report the new process ID."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .process import OBS_EXECUTABLES


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs-executable", type=Path, required=True)
    parser.add_argument("--portable", action="store_true", help="Use OBS portable configuration")
    parser.add_argument("--multi", action="store_true", help="Allow a second OBS instance")
    args = parser.parse_args(argv)
    try:
        executable = args.obs_executable.resolve(strict=True)
        if not executable.is_file() or executable.name.casefold() not in OBS_EXECUTABLES:
            parser.error("expected the OBS Studio executable")
        command = [str(executable)]
        if args.portable:
            command.append("--portable")
        if args.multi:
            command.append("--multi")
        process = subprocess.Popen(
            command,
            cwd=executable.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Could not launch OBS Studio ({type(exc).__name__})\n")
    print(json.dumps({"hostPid": process.pid}))


if __name__ == "__main__":
    main()
