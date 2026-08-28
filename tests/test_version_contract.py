from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from dcc_mcp_obs import __version__

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+"
RELEASE_VERSION_SURFACES = {
    "pyproject.toml",
    "buildspec.json",
    "native/src/plugin-main.cpp",
    "src/dcc_mcp_obs/__version__.py",
    "src/dcc_mcp_obs/skills/obs-control/SKILL.md",
}


def test_current_version_fixtures_do_not_embed_release_versions() -> None:
    plugin_fixture_paths = [
        "tests/test_bridge.py",
        "tests/test_dispatcher.py",
        "tests/test_mcp_integration.py",
        "tests/test_skill_contract.py",
        "tests/verify_installed_skill_contract.py",
    ]
    for relative in plugin_fixture_paths:
        source = (ROOT / relative).read_text(encoding="utf-8")
        if relative == "tests/test_bridge.py":
            foreign = '"pluginVersion": "999.0.0"'
            assert source.count(foreign) == 1
            source = source.replace(foreign, '"pluginVersion": FOREIGN_VERSION')
        assert not re.search(rf'["\']pluginVersion["\']\s*:\s*["\']{SEMVER}["\']', source)

    lifecycle = (ROOT / "tests" / "test_install_lifecycle.py").read_text(encoding="utf-8")
    foreign_receipt = '("version", "999.0.0")'
    assert lifecycle.count(foreign_receipt) == 1
    lifecycle = lifecycle.replace(foreign_receipt, '("version", FOREIGN_VERSION)')
    assert not re.search(rf'["\']version["\']\s*:\s*["\']{SEMVER}["\']', lifecycle)

    bundle = (ROOT / "tests" / "test_plugin_bundle.py").read_text(encoding="utf-8")
    assert not re.search(rf"""create_bundle\([^\n]*,[\s]*["']{SEMVER}["']""", bundle)


def test_release_owned_version_surfaces_match_canonical_package_version() -> None:
    release_config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    configured = release_config["packages"]["."]["extra-files"]
    configured_paths = {entry if isinstance(entry, str) else entry["path"] for entry in configured}
    assert configured_paths == RELEASE_VERSION_SURFACES

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    buildspec = json.loads((ROOT / "buildspec.json").read_text(encoding="utf-8"))
    native = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    native_match = re.search(
        r'constexpr char kPluginVersion\[\] = "([^"]+)"; // x-release-please-version',
        native,
    )
    assert native_match is not None
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    skill_metadata = yaml.safe_load(skill.split("---", maxsplit=2)[1])

    versions = {
        "pyproject.toml": pyproject["project"]["version"],
        "buildspec.json": buildspec["version"],
        "native/src/plugin-main.cpp": native_match.group(1),
        "src/dcc_mcp_obs/__version__.py": __version__,
        "src/dcc_mcp_obs/skills/obs-control/SKILL.md": skill_metadata["metadata"]["dcc-mcp"][
            "version"
        ],
    }
    assert versions == dict.fromkeys(RELEASE_VERSION_SURFACES, __version__)
