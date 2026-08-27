from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools.create_plugin_bundle import create_bundle


def test_windows_plugin_bundle_is_install_contract_compatible(tmp_path: Path) -> None:
    root = tmp_path / "release"
    binary = root / "dcc-mcp-obs" / "bin" / "64bit" / "dcc-mcp-obs.dll"
    locale = root / "dcc-mcp-obs" / "data" / "locale" / "en-US.ini"
    binary.parent.mkdir(parents=True)
    locale.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    locale.write_text('Plugin.Name="DCC-MCP OBS"', encoding="utf-8")
    output = tmp_path / "bundle.zip"

    create_bundle(root, "windows", "0.1.0", output)

    with zipfile.ZipFile(output) as bundle:
        manifest = json.loads(bundle.read("dcc-mcp-obs-plugin.json"))
        assert manifest["platform"] == "windows"
        assert {entry["target"] for entry in manifest["files"]} == {
            "bin/64bit/dcc-mcp-obs.dll",
            "data/locale/en-US.ini",
        }
        assert set(bundle.namelist()) == {
            "dcc-mcp-obs-plugin.json",
            "payload/bin/64bit/dcc-mcp-obs.dll",
            "payload/data/locale/en-US.ini",
        }
