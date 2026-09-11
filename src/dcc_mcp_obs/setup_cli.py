"""Environment setup helper for OBS Studio and the DCC-MCP adapter.

Discovery is always safe; installation and launch require explicit flags.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def discover_obs() -> dict[str, object]:
    candidates: list[Path] = []
    if value := os.environ.get("DCC_MCP_OBS_EXECUTABLE"):
        candidates.append(Path(value))
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if root:
            candidates.append(Path(root) / "obs-studio" / "bin" / "64bit" / "obs64.exe")
    if found := shutil.which("obs64.exe") or shutil.which("obs.exe"):
        candidates.append(Path(found))
    for path in candidates:
        if path.is_file():
            return {"status": "found", "executable": str(path.resolve())}
    return {"status": "missing", "executable": None}


def run(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Prepare OBS for DCC-MCP OBS")
    parser.add_argument("command", choices=("doctor", "install", "start", "status"))
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    state = discover_obs()
    if args.command in {"doctor", "status"}:
        return state
    if args.command == "install":
        if not args.yes:
            return {**state, "status": "consent_required", "next": "setup install --yes"}
        if state["status"] == "found":
            return {**state, "action": "already-installed"}
        if not shutil.which("winget"):
            return {**state, "status": "installer_unavailable", "reason": "winget_not_found"}
        subprocess.run(["winget", "install", "--id", "OBSProject.OBSStudio",
                        "--exact", "--scope", "user", "--accept-source-agreements",
                        "--accept-package-agreements"], check=True)
        return discover_obs()
    if state["status"] != "found":
        return {**state, "status": "missing", "reason": "run setup install --yes first"}
    process = subprocess.Popen([state["executable"], "--minimize-to-tray"], shell=False)
    return {**state, "status": "started", "pid": process.pid}


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))


__all__ = ["discover_obs", "run", "main"]
