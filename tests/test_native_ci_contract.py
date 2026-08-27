from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_build_script_preset_exists() -> None:
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    build_presets = {preset["name"]: preset for preset in presets["buildPresets"]}

    assert build_presets["windows-x64"]["configurePreset"] == "windows-x64"
    assert build_presets["windows-ci-x64"]["configurePreset"] == "windows-ci-x64"

    script = (ROOT / ".github" / "scripts" / "Build-Windows.ps1").read_text(encoding="utf-8")
    assert '"windows-${Target}"' in script


def test_native_macos_jobs_use_obs_compatible_runner() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    native_ci = ci.split("\n  native:\n", maxsplit=1)[1]

    assert "- os: macos-15\n            target: universal" in native_ci
    assert "- os: macos-15\n            target: universal" in release

    compiler_contract = (ROOT / "cmake" / "macos" / "compilerconfig.cmake").read_text(
        encoding="utf-8"
    )
    assert "set(obs_macos_minimum_sdk 15.0)" in compiler_contract
    assert "set(obs_macos_minimum_xcode 16.0)" in compiler_contract
