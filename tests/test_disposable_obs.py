from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tools.create_plugin_bundle import create_bundle

from dcc_mcp_obs import disposable_obs
from dcc_mcp_obs.disposable_obs import (
    DisposableObsError,
    DisposableObsLayout,
    build_disposable_layout,
    materialize_windows_plugin,
    prepare_obs_configuration,
    run_disposable_acceptance,
)


def test_windows_layout_copies_only_into_disposable_root(tmp_path: Path) -> None:
    source = tmp_path / "installed" / "obs-studio"
    executable = source / "bin" / "64bit" / "obs64.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"obs")
    (source / "data").mkdir()

    work_root = tmp_path / "acceptance"
    layout = build_disposable_layout(
        platform="windows",
        obs_executable=executable,
        work_root=work_root,
    )

    assert layout.executable == work_root / "obs" / "bin" / "64bit" / "obs64.exe"
    assert layout.config_root == work_root / "obs" / "config" / "obs-studio"
    assert layout.plugin_install_root == work_root / "native-plugin"
    assert layout.command == (
        str(layout.executable),
        "--portable",
        "--multi",
        "--disable-updater",
        "--disable-shutdown-check",
        "--disable-missing-files-check",
        "--minimize-to-tray",
    )
    assert layout.environment == {}
    assert executable.is_file()


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_posix_layout_isolates_home_and_config(tmp_path: Path, platform: str) -> None:
    executable = tmp_path / "installed" / "obs"
    executable.parent.mkdir()
    executable.write_bytes(b"obs")

    layout = build_disposable_layout(
        platform=platform,
        obs_executable=executable,
        work_root=tmp_path / "acceptance",
    )

    assert layout.executable == executable.resolve()
    assert layout.command[0] == str(executable.resolve())
    assert "--portable" not in layout.command
    assert "--multi" in layout.command
    assert Path(layout.environment["HOME"]).is_relative_to(layout.root)
    assert Path(layout.environment["XDG_CONFIG_HOME"]).is_relative_to(layout.root)
    if platform == "macos":
        assert layout.environment["CFFIXED_USER_HOME"] == layout.environment["HOME"]
    else:
        assert "CFFIXED_USER_HOME" not in layout.environment
    assert layout.config_root.is_relative_to(layout.root)
    assert layout.plugin_install_root.is_relative_to(layout.root)


def test_layout_rejects_nonempty_work_root(tmp_path: Path) -> None:
    executable = tmp_path / "obs64.exe"
    executable.write_bytes(b"obs")
    work_root = tmp_path / "acceptance"
    work_root.mkdir()
    (work_root / "foreign.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DisposableObsError, match=r"^OBS_DISPOSABLE_ROOT_NOT_EMPTY$"):
        build_disposable_layout(
            platform="windows",
            obs_executable=executable,
            work_root=work_root,
        )

    assert (work_root / "foreign.txt").read_text(encoding="utf-8") == "keep"


def test_configuration_enables_authenticated_loopback_recording(tmp_path: Path) -> None:
    root = tmp_path / "config"
    recordings = tmp_path / "recordings"
    prepare_obs_configuration(
        config_root=root,
        recordings=recordings,
        websocket_port=49231,
        websocket_password="private-test-password",
    )

    websocket_config = json.loads(
        (root / "plugin_config" / "obs-websocket" / "config.json").read_text()
    )
    assert websocket_config == {
        "alerts_enabled": False,
        "auth_required": True,
        "first_load": False,
        "server_enabled": True,
        "server_password": "private-test-password",
        "server_port": 49231,
    }
    profile = (root / "basic" / "profiles" / "DCC Acceptance" / "basic.ini").read_text()
    escaped_recordings = str(recordings.resolve()).replace("\\", "\\\\")
    assert f"FilePath={escaped_recordings}" in profile
    assert "BaseCX=640" in profile
    assert "BaseCY=360" in profile
    assert "OutputCX=640" in profile
    assert "OutputCY=360" in profile
    assert "RecEncoder=x264" in profile
    assert "RecFormat2=mkv" in profile
    assert recordings.is_dir()

    user = (root / "user.ini").read_text()
    assert "FirstRun=true" in user
    assert "ProfileDir=DCC Acceptance" in user
    scene = json.loads((root / "basic" / "scenes" / "DCC Acceptance.json").read_text())
    assert scene["current_scene"] == "Scene"
    assert scene["current_program_scene"] == "Scene"
    assert [item["name"] for item in scene["scene_order"]] == ["Scene"]


def test_configuration_rejects_invalid_secret_and_port(tmp_path: Path) -> None:
    with pytest.raises(DisposableObsError, match=r"^OBS_DISPOSABLE_CONFIG_INVALID$"):
        prepare_obs_configuration(
            config_root=tmp_path / "config",
            recordings=tmp_path / "recordings",
            websocket_port=80,
            websocket_password="short",
        )


@pytest.mark.parametrize(
    ("platform", "renderer"),
    [("win32", "Direct3D 11"), ("darwin", "Metal"), ("linux", "OpenGL")],
)
def test_configuration_selects_native_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, renderer: str
) -> None:
    monkeypatch.setattr(disposable_obs.sys, "platform", platform)
    config = tmp_path / platform

    prepare_obs_configuration(
        config_root=config,
        recordings=tmp_path / f"{platform}-recordings",
        websocket_port=49231,
        websocket_password="private-test-password",
    )

    global_config = (config / "global.ini").read_text(encoding="utf-8")
    assert f"Renderer={renderer}" in global_config
    if platform == "darwin":
        assert "MacOSPermissionsDialogLastShown=1" in global_config


def test_windows_plugin_mapping_uses_first_party_discovery_paths(tmp_path: Path) -> None:
    staged = tmp_path / "native-plugin"
    binary = staged / "bin" / "64bit" / "dcc-mcp-obs.dll"
    locale = staged / "data" / "locale" / "en-US.ini"
    binary.parent.mkdir(parents=True)
    locale.parent.mkdir(parents=True)
    binary.write_bytes(b"plugin")
    locale.write_text("locale", encoding="utf-8")
    obs_root = tmp_path / "obs"
    obs_root.mkdir()

    mapped = materialize_windows_plugin(staged, obs_root=obs_root)

    expected_binary = obs_root / "obs-plugins" / "64bit" / "dcc-mcp-obs.dll"
    expected_locale = obs_root / "data" / "obs-plugins" / "dcc-mcp-obs" / "locale" / "en-US.ini"
    assert mapped == (expected_binary, expected_locale)
    assert expected_binary.read_bytes() == b"plugin"
    assert expected_locale.read_text(encoding="utf-8") == "locale"


def test_windows_plugin_mapping_rejects_foreign_inventory(tmp_path: Path) -> None:
    staged = tmp_path / "native-plugin"
    binary = staged / "bin" / "64bit" / "dcc-mcp-obs.dll"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"plugin")
    (staged / "foreign.txt").write_text("foreign", encoding="utf-8")
    obs_root = tmp_path / "obs"
    obs_root.mkdir()

    with pytest.raises(DisposableObsError, match=r"^OBS_DISPOSABLE_PLUGIN_INVALID$"):
        materialize_windows_plugin(staged, obs_root=obs_root)


class _FakeProcess:
    pid = 48127

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise TimeoutError
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1


def _windows_bundle(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    binary = release / "dcc-mcp-obs" / "bin" / "64bit" / "dcc-mcp-obs.dll"
    locale = release / "dcc-mcp-obs" / "data" / "locale" / "en-US.ini"
    binary.parent.mkdir(parents=True)
    locale.parent.mkdir(parents=True)
    binary.write_bytes(b"plugin")
    locale.write_text("locale", encoding="utf-8")
    archive = tmp_path / "native.zip"
    create_bundle(release, "windows", "1.1.0", archive)
    return archive


def test_runner_keeps_secret_out_of_process_command_and_restores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "installed" / "obs-studio"
    executable = installed / "bin" / "64bit" / "obs64.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"obs")
    archive = _windows_bundle(tmp_path)
    wheel = tmp_path / "adapter.whl"
    wheel.write_bytes(b"wheel")
    output = tmp_path / "evidence.json"
    process = _FakeProcess()
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = tuple(command)
        captured["environment"] = dict(kwargs["env"])
        return process

    def fake_acceptance(**kwargs):
        captured["acceptance"] = kwargs
        assert os.environ["DCC_MCP_OBS_WEBSOCKET_PASSWORD"]
        assert os.environ["DCC_MCP_OBS_WEBSOCKET_URL"].startswith("ws://127.0.0.1:")
        process.returncode = 0
        return {"result": "passed"}

    def fake_install(layout: DisposableObsLayout, _archive: Path, platform: str) -> None:
        assert platform == "windows"
        mapped = layout.executable.parents[2] / "obs-plugins" / "64bit" / "dcc-mcp-obs.dll"
        mapped.parent.mkdir(parents=True)
        mapped.write_bytes(b"plugin")

    monkeypatch.setenv("DCC_MCP_OBS_WEBSOCKET_PASSWORD", "operator-owned")
    monkeypatch.setattr("dcc_mcp_obs.disposable_obs._install_plugin", fake_install)
    result = run_disposable_acceptance(
        obs_executable=executable,
        native_plugin_archive=archive,
        python_wheel=wheel,
        work_root=tmp_path / "acceptance",
        output=output,
        platform="windows",
        popen_factory=fake_popen,
        readiness_probe=lambda _process, _port: None,
        acceptance_runner=fake_acceptance,
    )

    assert result == {"result": "passed"}
    command = captured["command"]
    environment = captured["environment"]
    assert isinstance(command, tuple)
    assert isinstance(environment, dict)
    assert all("password" not in value.casefold() for value in command)
    assert "DCC_MCP_OBS_WEBSOCKET_PASSWORD" not in environment
    assert os.environ["DCC_MCP_OBS_WEBSOCKET_PASSWORD"] == "operator-owned"
    acceptance = captured["acceptance"]
    assert isinstance(acceptance, dict)
    assert acceptance["host_pid"] == process.pid
    root = Path(acceptance["disposable_root"])
    assert (root / "obs" / "obs-plugins" / "64bit" / "dcc-mcp-obs.dll").is_file()


def test_runner_terminates_only_spawned_process_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "acceptance"
    root.mkdir()
    executable = tmp_path / "obs64.exe"
    executable.write_bytes(b"obs")
    layout = DisposableObsLayout(
        root=root,
        executable=executable,
        config_root=root / "config",
        plugin_install_root=root / "plugin",
        recordings=root / "recordings",
        command=(str(executable), "--portable", "--multi"),
        environment={},
    )
    process = _FakeProcess()
    monkeypatch.setattr(
        "dcc_mcp_obs.disposable_obs.build_disposable_layout", lambda **_kwargs: layout
    )
    monkeypatch.setattr("dcc_mcp_obs.disposable_obs._install_plugin", lambda *_args: None)
    monkeypatch.setattr(
        "dcc_mcp_obs.disposable_obs.prepare_obs_configuration", lambda **_kwargs: None
    )

    with pytest.raises(RuntimeError, match="acceptance failed"):
        run_disposable_acceptance(
            obs_executable=executable,
            native_plugin_archive=tmp_path / "native.zip",
            python_wheel=tmp_path / "adapter.whl",
            work_root=root,
            output=tmp_path / "evidence.json",
            platform="windows",
            popen_factory=lambda *_args, **_kwargs: process,
            readiness_probe=lambda _process, _port: None,
            acceptance_runner=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("acceptance failed")
            ),
        )

    assert process.terminated is True
