from __future__ import annotations

import hashlib
import json
import runpy
import sys
from importlib import metadata
from pathlib import Path

import yaml
from tools import build_standalone

from dcc_mcp_obs import __version__, _standalone_entry

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_standalone_routes_sidecar_and_installer_without_system_python(
    monkeypatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        _standalone_entry.server,
        "main",
        lambda argv=None: calls.append(("sidecar", list(argv or []))),
    )
    monkeypatch.setattr(
        _standalone_entry.install_cli,
        "main",
        lambda argv=None: calls.append(("install", list(argv or []))),
    )

    _standalone_entry.main(["dcc-mcp-obs", "--host-pid", "123"])
    _standalone_entry.main(["dcc-mcp-obs", "install", "--dry-run"])

    assert calls == [
        ("sidecar", ["--host-pid", "123"]),
        ("install", ["install", "--dry-run"]),
    ]
    assert sys.executable == _standalone_entry.os.environ["DCC_MCP_PYTHON_EXECUTABLE"]


def test_standalone_installs_its_bundled_native_plugin(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "dcc-mcp-obs.exe"
    plugin = tmp_path / "dcc-mcp-obs-plugin.zip"
    plugin.write_bytes(b"native-plugin")
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs-standalone",
        "version": __version__,
        "core_version": metadata.version("dcc-mcp-core"),
        "platform": _standalone_entry.install_cli._platform_name(),
        "files": [
            {
                "path": plugin.name,
                "sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                "size": plugin.stat().st_size,
            }
        ],
    }
    (tmp_path / "dcc-mcp-obs-standalone.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(_standalone_entry.sys, "executable", str(executable))
    monkeypatch.setattr(
        _standalone_entry.install_cli,
        "main",
        lambda argv=None: calls.append(list(argv or [])),
    )

    _standalone_entry.main(
        ["dcc-mcp-obs.exe", "install-bundled", "--plugin-dir", str(tmp_path / "plugin")]
    )

    assert calls == [
        [
            "install",
            "--plugin-archive",
            str(plugin),
            "--sha256",
            manifest["files"][0]["sha256"],
            "--plugin-dir",
            str(tmp_path / "plugin"),
        ]
    ]


def test_standalone_executes_core_managed_skill_scripts(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "skill.py"
    output = tmp_path / "result.json"
    script.write_text(
        "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv))\n",
        encoding="utf-8",
    )
    original_argv = list(sys.argv)
    monkeypatch.setattr(runpy, "run_path", runpy.run_path)

    _standalone_entry.main(["dcc-mcp-obs", str(script), str(output), "cue"])

    assert json.loads(output.read_text(encoding="utf-8")) == [
        str(script.resolve()),
        str(output),
        "cue",
    ]
    assert sys.argv == original_argv


def test_pyoxidizer_contract_bundles_adapter_and_dependencies() -> None:
    config = (ROOT / "pyoxidizer.bzl").read_text(encoding="utf-8")

    assert 'name="dcc-mcp-obs"' in config
    assert 'python_config.run_module = "dcc_mcp_obs._standalone_entry"' in config
    assert 'policy.resources_location = "filesystem-relative:lib"' in config
    assert 'python_config.module_search_paths = ["$ORIGIN/lib"]' in config
    assert "exe.pip_install([" in config
    assert '"dcc-mcp-core=={}".format(VARS["core_version"])' in config
    builder = (ROOT / "tools/build_standalone.py").read_text(encoding="utf-8")
    assert '"build",\n            "--release"' in builder
    standalone = tomllib.loads((ROOT / "packaging/standalone.toml").read_text(encoding="utf-8"))
    assert standalone["core_version"] == "0.20.22"


def test_standalone_archive_preserves_full_semantic_version(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "standalone"
    output.mkdir()
    (output / "dcc-mcp-obs.exe").write_bytes(b"executable")
    monkeypatch.setattr(build_standalone, "DIST", tmp_path)
    monkeypatch.setattr(build_standalone, "OUTPUT", output)

    archive = build_standalone.create_archive("windows", "1.1.0")

    assert archive.name == "dcc-mcp-obs-1.1.0-windows-standalone.zip"


def test_ci_and_release_build_all_standalone_platforms() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))

    ci_job = ci["jobs"]["standalone"]
    release_job = release["jobs"]["standalone-artifacts"]
    for job in (ci_job, release_job):
        platforms = {entry["platform"] for entry in job["strategy"]["matrix"]["include"]}
        assert platforms == {"windows", "macos", "linux"}
        scripts = "\n".join(str(step.get("run", "")) for step in job["steps"])
        assert "tools/build_standalone.py" in scripts
        assert "--version" in scripts
        assert "install --help" in scripts
        assert "tools/standalone_skill_smoke.py" in scripts

    assert release_job["needs"] == ["identity", "native-artifacts"]
    assert release["jobs"]["publish"]["needs"] == [
        "identity",
        "python-artifacts",
        "native-artifacts",
        "standalone-artifacts",
    ]


def test_cli_install_runbook_selects_the_bundled_runtime_and_environment_override() -> None:
    runbook = (ROOT / "install.md").read_text(encoding="utf-8")
    detailed = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")

    for text in (runbook, detailed):
        assert "dcc-mcp-cli install --dcc-type obs" in text
        assert "DCC_MCP_OBS_EXECUTABLE" in text
        assert "DCC_MCP_PYTHON_EXECUTABLE" in text
        assert "install-bundled" in text
        assert "dcc-mcp-cli wait-ready --dcc-type obs" in text

    assert "Python 3.10+ remains optional" in runbook
