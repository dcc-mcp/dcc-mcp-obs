"""Create an isolated OBS host for real release acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .install_cli import run as run_installer
from .real_obs_acceptance import run_real_obs_acceptance


class DisposableObsError(RuntimeError):
    """Stable fail-closed error raised before a disposable OBS launch."""


@dataclass(frozen=True)
class DisposableObsLayout:
    root: Path
    executable: Path
    config_root: Path
    plugin_install_root: Path
    recordings: Path
    command: tuple[str, ...]
    environment: dict[str, str]


_COMMON_ARGUMENTS = (
    "--multi",
    "--disable-updater",
    "--disable-shutdown-check",
    "--disable-missing-files-check",
    "--minimize-to-tray",
)


def _fail(code: str) -> None:
    raise DisposableObsError(code)


def _empty_root(path: Path) -> Path:
    root = path.resolve(strict=False)
    try:
        if root.exists():
            if not root.is_dir() or any(root.iterdir()):
                _fail("OBS_DISPOSABLE_ROOT_NOT_EMPTY")
        else:
            root.mkdir(parents=True)
    except DisposableObsError:
        raise
    except OSError as exc:
        raise DisposableObsError("OBS_DISPOSABLE_ROOT_INVALID") from exc
    return root


def build_disposable_layout(
    *,
    platform: str,
    obs_executable: Path,
    work_root: Path,
) -> DisposableObsLayout:
    """Create platform-specific roots without touching operator OBS state."""

    root = _empty_root(work_root)
    try:
        executable = obs_executable.resolve(strict=True)
        if not executable.is_file() or executable.is_symlink():
            _fail("OBS_DISPOSABLE_EXECUTABLE_INVALID")
        environment: dict[str, str] = {}
        if platform == "windows":
            if executable.name.casefold() != "obs64.exe" or len(executable.parents) < 3:
                _fail("OBS_DISPOSABLE_EXECUTABLE_INVALID")
            source_root = executable.parents[2]
            if executable.relative_to(source_root).as_posix().casefold() != "bin/64bit/obs64.exe":
                _fail("OBS_DISPOSABLE_EXECUTABLE_INVALID")
            obs_root = root / "obs"
            shutil.copytree(source_root, obs_root)
            executable = obs_root / "bin" / "64bit" / "obs64.exe"
            config_root = obs_root / "config" / "obs-studio"
            plugin_root = root / "native-plugin"
            command = (str(executable), "--portable", *_COMMON_ARGUMENTS)
        elif platform in {"linux", "macos"}:
            home = root / "home"
            xdg_config = root / "xdg-config"
            home.mkdir()
            xdg_config.mkdir()
            environment = {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(xdg_config),
            }
            if platform == "macos":
                # OBS resolves its user-domain config through CoreFoundation,
                # which intentionally prefers this override over HOME.
                environment["CFFIXED_USER_HOME"] = str(home)
                config_root = home / "Library" / "Application Support" / "obs-studio"
                plugin_root = config_root / "plugins" / "dcc-mcp-obs.plugin"
            else:
                config_root = xdg_config / "obs-studio"
                plugin_root = config_root / "plugins" / "dcc-mcp-obs"
            command = (str(executable), *_COMMON_ARGUMENTS)
        else:
            _fail("OBS_DISPOSABLE_PLATFORM_INVALID")
        recordings = root / "recordings"
    except DisposableObsError:
        raise
    except (OSError, ValueError) as exc:
        raise DisposableObsError("OBS_DISPOSABLE_LAYOUT_FAILED") from exc
    return DisposableObsLayout(
        root=root,
        executable=executable,
        config_root=config_root,
        plugin_install_root=plugin_root,
        recordings=recordings,
        command=command,
        environment=environment,
    )


def prepare_obs_configuration(
    *,
    config_root: Path,
    recordings: Path,
    websocket_port: int,
    websocket_password: str,
) -> None:
    """Write a minimal, authenticated OBS profile inside the isolated root."""

    if (
        not isinstance(websocket_port, int)
        or isinstance(websocket_port, bool)
        or not 1024 <= websocket_port <= 65535
        or not isinstance(websocket_password, str)
        or not 16 <= len(websocket_password) <= 256
        or any(character in websocket_password for character in "\r\n\0")
    ):
        _fail("OBS_DISPOSABLE_CONFIG_INVALID")
    try:
        config_root = config_root.resolve(strict=False)
        recordings = recordings.resolve(strict=False)
        recordings.mkdir(parents=True)
        profile = config_root / "basic" / "profiles" / "DCC Acceptance"
        scenes = config_root / "basic" / "scenes"
        websocket = config_root / "plugin_config" / "obs-websocket"
        profile.mkdir(parents=True)
        scenes.mkdir(parents=True)
        websocket.mkdir(parents=True)
        recording_config_value = str(recordings).replace("\\", "\\\\")
        renderer = (
            "Direct3D 11"
            if sys.platform == "win32"
            else "Metal"
            if sys.platform == "darwin"
            else "OpenGL"
        )
        macos_permission_state = (
            "MacOSPermissionsDialogLastShown=1\n" if sys.platform == "darwin" else ""
        )

        (config_root / "global.ini").write_text(
            "[General]\n"
            "Pre31Migrated=true\n"
            "EnableAutoUpdates=false\n"
            "ProcessPriority=Normal\n"
            f"{macos_permission_state}"
            "\n[Video]\n"
            f"Renderer={renderer}\n",
            encoding="utf-8",
        )
        (config_root / "user.ini").write_text(
            "[General]\n"
            "FirstRun=true\n"
            "ConfirmOnExit=false\n"
            "\n[Basic]\n"
            "Profile=DCC Acceptance\n"
            "ProfileDir=DCC Acceptance\n"
            "SceneCollection=DCC Acceptance\n"
            "SceneCollectionFile=DCC Acceptance\n"
            "\n[BasicWindow]\n"
            "PreviewProgramMode=false\n"
            "WarnBeforeStoppingRecord=false\n"
            "SysTrayWhenStarted=true\n"
            "SysTrayMinimizeToTray=true\n",
            encoding="utf-8",
        )
        (profile / "basic.ini").write_text(
            "[General]\n"
            "Name=DCC Acceptance\n"
            "\n[Output]\n"
            "Mode=Simple\n"
            "FilenameFormatting=acceptance-%CCYY-%MM-%DD-%hh-%mm-%ss\n"
            "\n[SimpleOutput]\n"
            f"FilePath={recording_config_value}\n"
            "RecFormat2=mkv\n"
            "RecQuality=Small\n"
            "RecEncoder=x264\n"
            "RecAudioEncoder=aac\n"
            "\n[Video]\n"
            "BaseCX=640\n"
            "BaseCY=360\n"
            "OutputCX=640\n"
            "OutputCY=360\n"
            "FPSType=0\n"
            "FPSCommon=30\n"
            "\n[Audio]\n"
            "SampleRate=48000\n"
            "ChannelSetup=Stereo\n",
            encoding="utf-8",
        )
        scene_uuid = str(uuid.uuid4())
        scene = {
            "name": "DCC Acceptance",
            "sources": [
                {
                    "prev_ver": 0,
                    "name": "Scene",
                    "uuid": scene_uuid,
                    "id": "scene",
                    "versioned_id": "scene",
                    "settings": {"id_counter": 1, "custom_size": False, "items": []},
                    "mixers": 0,
                    "sync": 0,
                    "flags": 0,
                    "volume": 1.0,
                    "balance": 0.5,
                    "enabled": True,
                    "muted": False,
                    "hotkeys": {"OBSBasic.SelectScene": []},
                    "private_settings": {},
                }
            ],
            "groups": [],
            "scene_order": [{"name": "Scene"}],
            "current_scene": "Scene",
            "current_program_scene": "Scene",
            "current_transition": "Fade",
            "transition_duration": 300,
            "transitions": [],
            "quick_transitions": [],
            "saved_projectors": [],
            "preview_locked": False,
            "modules": {},
            "resolution": {"x": 640, "y": 360},
            "version": 2,
        }
        (scenes / "DCC Acceptance.json").write_text(
            json.dumps(scene, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        (websocket / "config.json").write_text(
            json.dumps(
                {
                    "alerts_enabled": False,
                    "auth_required": True,
                    "first_load": False,
                    "server_enabled": True,
                    "server_password": websocket_password,
                    "server_port": websocket_port,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise DisposableObsError("OBS_DISPOSABLE_CONFIG_FAILED") from exc


def materialize_windows_plugin(staged: Path, *, obs_root: Path) -> tuple[Path, ...]:
    """Map a verified installer layout into portable OBS first-party paths."""

    try:
        staged = staged.resolve(strict=True)
        obs_root = obs_root.resolve(strict=True)
        if not staged.is_dir() or not obs_root.is_dir():
            _fail("OBS_DISPOSABLE_PLUGIN_INVALID")
        files = sorted(path for path in staged.rglob("*") if path.is_file())
        relatives = [path.relative_to(staged).as_posix() for path in files]
        allowed = [
            relative == ".dcc-mcp-obs-install.json"
            or relative == "bin/64bit/dcc-mcp-obs.dll"
            or relative.startswith("data/")
            for relative in relatives
        ]
        if not files or not all(allowed) or "bin/64bit/dcc-mcp-obs.dll" not in relatives:
            _fail("OBS_DISPOSABLE_PLUGIN_INVALID")
        mapped: list[Path] = []
        for source, relative in zip(files, relatives, strict=True):
            if relative == ".dcc-mcp-obs-install.json":
                continue
            if relative == "bin/64bit/dcc-mcp-obs.dll":
                destination = obs_root / "obs-plugins" / "64bit" / "dcc-mcp-obs.dll"
            else:
                destination = obs_root / "data" / "obs-plugins" / "dcc-mcp-obs" / Path(relative[5:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            mapped.append(destination)
        return tuple(mapped)
    except DisposableObsError:
        raise
    except (OSError, ValueError) as exc:
        raise DisposableObsError("OBS_DISPOSABLE_PLUGIN_INVALID") from exc


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _install_plugin(
    layout: DisposableObsLayout,
    archive: Path,
    platform: str,
) -> None:
    try:
        archive = archive.resolve(strict=True)
        if not archive.is_file():
            _fail("OBS_DISPOSABLE_PLUGIN_INVALID")
        code, report = run_installer(
            [
                "install",
                "--plugin-archive",
                str(archive),
                "--sha256",
                _sha256_path(archive),
                "--plugin-dir",
                str(layout.plugin_install_root),
            ]
        )
        if code != 0 or report.get("status") != "requires_restart":
            _fail("OBS_DISPOSABLE_PLUGIN_INSTALL_FAILED")
        del report
        if platform == "windows":
            materialize_windows_plugin(
                layout.plugin_install_root, obs_root=layout.executable.parents[2]
            )
    except DisposableObsError:
        raise
    except OSError as exc:
        raise DisposableObsError("OBS_DISPOSABLE_PLUGIN_INSTALL_FAILED") from exc


def _reserve_loopback_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])
    except OSError as exc:
        raise DisposableObsError("OBS_DISPOSABLE_PORT_UNAVAILABLE") from exc


def _wait_for_obs(process: Any, port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail("OBS_DISPOSABLE_HOST_EXITED")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    _fail("OBS_DISPOSABLE_HOST_TIMEOUT")


@contextmanager
def _scoped_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _terminate_spawned_process(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except (subprocess.TimeoutExpired, TimeoutError):
        process.kill()
        process.wait(timeout=8)


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def run_disposable_acceptance(
    *,
    obs_executable: Path,
    native_plugin_archive: Path,
    python_wheel: Path,
    work_root: Path,
    output: Path,
    platform: str | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    readiness_probe: Callable[[Any, int], None] = _wait_for_obs,
    acceptance_runner: Callable[..., dict[str, object]] = run_real_obs_acceptance,
) -> dict[str, object]:
    """Launch, exercise, and gracefully stop one disposable OBS process."""

    selected_platform = platform or _platform_name()
    layout = build_disposable_layout(
        platform=selected_platform,
        obs_executable=obs_executable,
        work_root=work_root,
    )
    port = _reserve_loopback_port()
    password = secrets.token_urlsafe(32)
    prepare_obs_configuration(
        config_root=layout.config_root,
        recordings=layout.recordings,
        websocket_port=port,
        websocket_password=password,
    )
    _install_plugin(layout, native_plugin_archive, selected_platform)

    child_environment = dict(os.environ)
    child_environment.update(layout.environment)
    child_environment.pop("DCC_MCP_OBS_WEBSOCKET_PASSWORD", None)
    child_environment.pop("DCC_MCP_OBS_WEBSOCKET_URL", None)
    process = None
    stdout_path = layout.root / "obs-stdout.log"
    stderr_path = layout.root / "obs-stderr.log"
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = popen_factory(
                layout.command,
                cwd=layout.executable.parent,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
        readiness_probe(process, port)
        with _scoped_environment(
            {
                "DCC_MCP_OBS_WEBSOCKET_URL": f"ws://127.0.0.1:{port}",
                "DCC_MCP_OBS_WEBSOCKET_PASSWORD": password,
                "DCC_MCP_OBS_TIMEOUT_SECONDS": "10",
            }
        ):
            return acceptance_runner(
                host_pid=process.pid,
                native_plugin_archive=native_plugin_archive,
                python_wheel=python_wheel,
                disposable_root=layout.root,
                output=output,
            )
    except BaseException:
        if process is not None:
            _terminate_spawned_process(process)
        raise


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch disposable OBS and publish privacy-safe real-host evidence."
    )
    parser.add_argument("--obs-executable", type=Path, required=True)
    parser.add_argument("--native-plugin-archive", type=Path, required=True)
    parser.add_argument("--python-wheel", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        evidence = run_disposable_acceptance(
            obs_executable=args.obs_executable,
            native_plugin_archive=args.native_plugin_archive,
            python_wheel=args.python_wheel,
            work_root=args.work_root,
            output=args.output,
        )
    except (DisposableObsError, Exception) as exc:
        code = str(exc)
        if not code.startswith("OBS_"):
            code = "OBS_DISPOSABLE_ACCEPTANCE_FAILED"
        print(json.dumps({"ok": False, "code": code}, sort_keys=True, separators=(",", ":")))
        raise SystemExit(1) from None
    print(json.dumps({"ok": True, "evidence": evidence}, sort_keys=True, separators=(",", ":")))


__all__ = [
    "DisposableObsError",
    "DisposableObsLayout",
    "build_disposable_layout",
    "main",
    "materialize_windows_plugin",
    "prepare_obs_configuration",
    "run_disposable_acceptance",
]
