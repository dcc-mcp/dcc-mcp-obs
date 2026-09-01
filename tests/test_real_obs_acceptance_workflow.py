from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "real-obs-acceptance.yml"


def test_real_obs_workflow_runs_packaged_artifacts_on_every_supported_host() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    job = workflow["jobs"]["real-obs"]
    matrix = job["strategy"]["matrix"]["include"]

    assert {(entry["platform"], entry["os"]) for entry in matrix} == {
        ("windows", "windows-2022"),
        ("macos", "macos-15"),
        ("linux", "ubuntu-24.04"),
    }
    assert "uses: ./.github/actions/build-plugin" in source
    assert "python tools/create_plugin_bundle.py" in source
    assert "python -m build --wheel" in source
    assert "dcc-mcp-obs-accept-host" in source
    assert "xvfb-run -a" in source
    assert "--native-plugin-archive" in source
    assert "--python-wheel" in source
    assert "--work-root" in source
    assert "--output" in source


def test_real_obs_workflow_publishes_only_privacy_safe_evidence() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "name: real-obs-evidence-${{ matrix.platform }}" in source
    assert "path: ${{ runner.temp }}/real-obs/evidence.json" in source
    assert "if-no-files-found: error" in source
    assert "obs-stdout.log" not in source
    assert "obs-stderr.log" not in source
    assert "DCC_MCP_OBS_WEBSOCKET_PASSWORD" not in source
    assert "password:" not in source.casefold()


def test_real_obs_workflow_pins_third_party_actions() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in source
    assert "actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38" in source
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in source
    assert "@v" not in "\n".join(
        line for line in source.splitlines() if line.lstrip().startswith("uses:")
    )
