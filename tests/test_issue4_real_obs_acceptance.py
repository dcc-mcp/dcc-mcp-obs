from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dcc_mcp_obs import server
from dcc_mcp_obs.real_obs_acceptance import (
    AcceptanceContractError,
    McpAcceptanceClient,
    _loaded_native_module_paths,
    await_authenticated_status,
    build_public_evidence,
    exercise_live_obs,
    verify_installed_python_wheel,
    verify_loaded_native_plugin,
)
from dcc_mcp_obs.skills.obs_control.scripts import _client

ARTIFACT_DIGEST = "a" * 64
WHEEL_DIGEST = "b" * 64
RECORDING_DIGEST = hashlib.sha256(b"real-obs-recording").hexdigest()


def test_authenticated_readiness_retries_until_native_plugin_is_ready() -> None:
    attempts = iter(
        [
            RuntimeError("vendor not ready"),
            {"instanceId": "isolated-instance", "ready": True},
        ]
    )
    sleeps: list[float] = []

    def probe() -> dict[str, object]:
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    assert await_authenticated_status(probe, sleeper=sleeps.append, attempts=3) == {
        "instanceId": "isolated-instance",
        "ready": True,
    }
    assert sleeps == [0.25]


def test_authenticated_readiness_fails_closed_after_timeout() -> None:
    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_READINESS_TIMEOUT$"):
        await_authenticated_status(
            lambda: (_ for _ in ()).throw(RuntimeError("not ready")),
            sleeper=lambda _seconds: None,
            attempts=2,
        )


def test_macos_loaded_module_proof_uses_vmmap_for_exact_process(tmp_path: Path) -> None:
    binary = (
        tmp_path
        / "home"
        / "Library"
        / "Application Support"
        / "obs-studio"
        / "plugins"
        / "dcc-mcp-obs.plugin"
        / "Contents"
        / "MacOS"
        / "dcc-mcp-obs"
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=f"mapped {binary.resolve()}\n")

    mapped = _loaded_native_module_paths(
        SimpleNamespace(pid=48127), platform="macos", disposable_root=tmp_path, runner=run
    )

    assert mapped == [binary.resolve()]
    assert calls == [
        (
            ["/usr/bin/vmmap", "-w", "48127"],
            {"capture_output": True, "text": True, "timeout": 10, "check": False},
        )
    ]


def test_macos_loaded_module_proof_fails_closed_without_exact_mapping(tmp_path: Path) -> None:
    binary = (
        tmp_path
        / "home"
        / "Library"
        / "Application Support"
        / "obs-studio"
        / "plugins"
        / "dcc-mcp-obs.plugin"
        / "Contents"
        / "MacOS"
        / "dcc-mcp-obs"
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native")

    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH$"):
        _loaded_native_module_paths(
            SimpleNamespace(pid=48127),
            platform="macos",
            disposable_root=tmp_path,
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="other"),
        )


def _completed_observation(tmp_path: Path) -> dict[str, object]:
    recording = tmp_path / "acceptance-recording.mkv"
    recording.write_bytes(b"real-obs-recording")
    return {
        "platform": "windows",
        "architecture": "x86_64",
        "obs_version": "32.2.1",
        "plugin_version": "1.1.0",
        "adapter_version": "1.1.0",
        "native_plugin_sha256": ARTIFACT_DIGEST,
        "python_wheel_sha256": WHEEL_DIGEST,
        "loaded_native_artifact_verified": True,
        "installed_python_wheel_verified": True,
        "host_pid": 12345,
        "plugin_instance_id": "01234567-89ab-cdef-0123-456789abcdef",
        "adapter_session_id": "abcdef01-2345-6789-abcd-ef0123456789",
        "authenticated": True,
        "exact_host_process_bound": True,
        "exact_plugin_instance_bound": True,
        "exact_adapter_session_bound": True,
        "scene_created": True,
        "scene_readback_verified": True,
        "source_created": True,
        "source_readback_verified": True,
        "scene_item_crud_verified": True,
        "transition_readback_verified": True,
        "studio_mode_preview_program_verified": True,
        "recording_states": ["stopped", "recording", "paused", "recording", "stopped"],
        "recording_output_path": str(recording),
        "recording_output_finalized": True,
        "recording_sha256": RECORDING_DIGEST,
        "recording_size_bytes": recording.stat().st_size,
    }


def test_public_evidence_proves_complete_real_obs_flow_without_sensitive_values(
    tmp_path: Path,
) -> None:
    observed = _completed_observation(tmp_path)

    evidence = build_public_evidence(observed, salt=b"acceptance-run-salt")

    assert evidence["schemaVersion"] == 1
    assert evidence["result"] == "passed"
    assert evidence["platform"] == {"name": "windows", "architecture": "x86_64"}
    assert evidence["artifacts"] == {
        "nativePluginSha256": ARTIFACT_DIGEST,
        "pythonWheelSha256": WHEEL_DIGEST,
    }
    assert evidence["versions"] == {
        "obs": "32.2.1",
        "nativePlugin": "1.1.0",
        "pythonAdapter": "1.1.0",
    }
    assert evidence["binding"] == {
        "hostProcess": {
            "verified": True,
            "fingerprint": hashlib.sha256(b"acceptance-run-salt\0host\0" + b"12345").hexdigest(),
        },
        "pluginInstance": {
            "verified": True,
            "fingerprint": hashlib.sha256(
                b"acceptance-run-salt\0plugin\0" + b"01234567-89ab-cdef-0123-456789abcdef"
            ).hexdigest(),
        },
        "adapterSession": {
            "verified": True,
            "fingerprint": hashlib.sha256(
                b"acceptance-run-salt\0session\0" + b"abcdef01-2345-6789-abcd-ef0123456789"
            ).hexdigest(),
        },
    }
    assert evidence["checks"] == {
        "authenticatedReadiness": True,
        "loadedNativeArtifact": True,
        "installedPythonWheel": True,
        "sceneCreateAndReadback": True,
        "sourceCreateAndReadback": True,
        "sceneItemCrudAndReadback": True,
        "transitionReadback": True,
        "studioModePreviewProgram": True,
        "recordingStart": True,
        "recordingPause": True,
        "recordingResume": True,
        "recordingStop": True,
        "recordingOutputFinalized": True,
    }
    assert evidence["recording"] == {
        "sha256": RECORDING_DIGEST,
        "sizeBytes": len(b"real-obs-recording"),
    }
    serialized = json.dumps(evidence, sort_keys=True)
    for secret in (
        str(observed["host_pid"]),
        str(observed["plugin_instance_id"]),
        str(observed["adapter_session_id"]),
        str(observed["recording_output_path"]),
    ):
        assert secret not in serialized


def test_checked_in_windows_evidence_matches_public_contract() -> None:
    path = Path(__file__).parents[1] / "docs" / "evidence" / "real-obs" / "windows-obs-32.2.1.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))

    assert evidence["result"] == "passed"
    assert evidence["platform"] == {"architecture": "amd64", "name": "windows"}
    assert all(evidence["checks"].values())
    assert all(binding["verified"] is True for binding in evidence["binding"].values())
    assert evidence["recording"]["sizeBytes"] > 0
    serialized = json.dumps(evidence, sort_keys=True).casefold()
    for forbidden in (
        "hostpid",
        "processid",
        "instanceid",
        "sessionid",
        "password",
        "hostname",
        "commandline",
        "localpath",
        "screenshot",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("authenticated", False, "OBS_ACCEPTANCE_AUTHENTICATION_REQUIRED"),
        (
            "loaded_native_artifact_verified",
            False,
            "OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH",
        ),
        (
            "installed_python_wheel_verified",
            False,
            "OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH",
        ),
        ("exact_host_process_bound", False, "OBS_ACCEPTANCE_HOST_BINDING_FAILED"),
        ("exact_plugin_instance_bound", False, "OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED"),
        ("exact_adapter_session_bound", False, "OBS_ACCEPTANCE_SESSION_BINDING_FAILED"),
        ("scene_readback_verified", False, "OBS_ACCEPTANCE_SCENE_READBACK_FAILED"),
        ("source_readback_verified", False, "OBS_ACCEPTANCE_SOURCE_READBACK_FAILED"),
        ("scene_item_crud_verified", False, "OBS_ACCEPTANCE_SCENE_GRAPH_FAILED"),
        ("transition_readback_verified", False, "OBS_ACCEPTANCE_TRANSITION_FAILED"),
        (
            "studio_mode_preview_program_verified",
            False,
            "OBS_ACCEPTANCE_STUDIO_MODE_FAILED",
        ),
        ("recording_output_finalized", False, "OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED"),
    ],
)
def test_public_evidence_fails_closed_when_a_required_real_obs_proof_is_missing(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    observed = _completed_observation(tmp_path)
    observed[field] = value

    with pytest.raises(AcceptanceContractError, match=f"^{code}$"):
        build_public_evidence(observed, salt=b"acceptance-run-salt")


def test_public_evidence_rejects_incomplete_recording_state_machine(tmp_path: Path) -> None:
    observed = _completed_observation(tmp_path)
    observed["recording_states"] = ["stopped", "recording", "stopped"]

    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_RECORDING_FLOW_FAILED$"):
        build_public_evidence(observed, salt=b"acceptance-run-salt")


def test_real_obs_acceptance_cli_is_part_of_the_installed_adapter() -> None:
    project = Path(__file__).parents[1] / "pyproject.toml"
    text = project.read_text(encoding="utf-8")

    assert 'dcc-mcp-obs-accept = "dcc_mcp_obs.real_obs_acceptance:main"' in text


class FakeAcceptanceClient:
    def __init__(self, output: Path, *, drift_at: str | None = None) -> None:
        self.output = output
        self.drift_at = drift_at
        self.session_id = "01234567-89ab-cdef-0123-456789abcdef"
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, dict(arguments)))
        identity = {
            "instanceId": (
                "drifted-instance"
                if self.drift_at == name
                else "01234567-89ab-cdef-0123-456789abcdef"
            ),
            "pluginVersion": "1.1.0",
            "obsVersion": "32.2.1",
            "hostPid": 12345,
            "eventSequence": len(self.calls),
            "ok": True,
        }
        context: dict[str, object] = dict(identity)
        verified = name in {
            "obs_control__create_scene",
            "obs_control__set_current_scene",
            "obs_control__create_source",
            "obs_control__set_scene_item_transform",
            "obs_control__remove_scene_item",
            "obs_control__set_current_transition",
            "obs_control__trigger_transition",
            "obs_control__set_studio_mode",
            "obs_control__set_preview_scene",
            "obs_control__transition_to_program",
            "obs_control__start_recording",
            "obs_control__pause_recording",
            "obs_control__resume_recording",
            "obs_control__stop_recording",
            "obs_control__remove_source",
            "obs_control__remove_scene",
        }
        if name == "obs_control__get_status":
            context["ready"] = True
        elif name == "obs_control__list_scene_items":
            context.update(
                {
                    "sceneName": arguments["scene_name"],
                    "sceneItems": [
                        {
                            "sceneItemId": 7,
                            "sourceName": "DCC Acceptance Color",
                            "sourceKind": "color_source_v3",
                            "enabled": True,
                        }
                    ],
                    "truncated": False,
                }
            )
        elif name == "obs_control__list_transitions":
            context.update(
                {
                    "transitions": [
                        {"transitionName": "Fade", "transitionKind": "fade_transition"}
                    ],
                    "currentTransitionName": "Fade",
                    "truncated": False,
                }
            )
        elif name == "obs_control__capture_program_frame":
            context.update(
                {
                    "imageWidth": 320,
                    "imageHeight": 180,
                    "byteLength": 1024,
                    "sha256": "d" * 64,
                }
            )
        elif name == "obs_control__get_recording_status":
            context.update(
                {
                    "outputActive": False,
                    "outputPaused": False,
                    "outputPath": "",
                    "totalBytes": 0,
                    "totalFrames": 0,
                    "lastError": "",
                }
            )
        elif name == "obs_control__start_recording":
            context.update({"outputActive": True, "outputPaused": False})
        elif name == "obs_control__pause_recording":
            context.update({"outputActive": True, "outputPaused": True})
        elif name == "obs_control__resume_recording":
            context.update({"outputActive": True, "outputPaused": False})
        elif name == "obs_control__stop_recording":
            self.output.write_bytes(b"real-obs-recording")
            context.update(
                {
                    "outputActive": False,
                    "outputPaused": False,
                    "outputPath": str(self.output),
                    "totalBytes": self.output.stat().st_size,
                    "totalFrames": 30,
                    "lastError": "",
                }
            )
        envelope: dict[str, object] = {
            "success": True,
            "message": "accepted",
            "error": None,
            "prompt": None,
            "context": context,
        }
        if verified:
            envelope["postcondition"] = {"verified": True}
        return envelope


def test_live_flow_exercises_issue4_and_issue6_through_one_exact_session(tmp_path: Path) -> None:
    client = FakeAcceptanceClient(tmp_path / "acceptance.mkv")

    observed = exercise_live_obs(
        client,
        host_pid=12345,
        platform="windows",
        architecture="x86_64",
        native_plugin_sha256=ARTIFACT_DIGEST,
        python_wheel_sha256=WHEEL_DIGEST,
        output_root=tmp_path,
        authenticated=True,
        loaded_native_artifact_verified=True,
        installed_python_wheel_verified=True,
        sleeper=lambda _seconds: None,
    )

    assert observed["recording_states"] == [
        "stopped",
        "recording",
        "paused",
        "recording",
        "stopped",
    ]
    assert observed["plugin_instance_id"] == "01234567-89ab-cdef-0123-456789abcdef"
    assert observed["adapter_session_id"] == client.session_id
    assert observed["scene_item_crud_verified"] is True
    assert observed["transition_readback_verified"] is True
    assert observed["studio_mode_preview_program_verified"] is True
    assert observed["recording_output_finalized"] is True
    assert [name for name, _arguments in client.calls] == [
        "obs_control__get_status",
        "obs_control__create_scene",
        "obs_control__create_scene",
        "obs_control__create_scene",
        "obs_control__list_transitions",
        "obs_control__set_current_transition",
        "obs_control__set_current_scene",
        "obs_control__create_source",
        "obs_control__list_scene_items",
        "obs_control__set_scene_item_transform",
        "obs_control__trigger_transition",
        "obs_control__set_studio_mode",
        "obs_control__set_preview_scene",
        "obs_control__transition_to_program",
        "obs_control__set_studio_mode",
        "obs_control__set_current_scene",
        "obs_control__capture_program_frame",
        "obs_control__get_recording_status",
        "obs_control__start_recording",
        "obs_control__pause_recording",
        "obs_control__resume_recording",
        "obs_control__stop_recording",
        "obs_control__set_current_scene",
        "obs_control__remove_scene_item",
        "obs_control__list_sources",
        "obs_control__remove_scene",
        "obs_control__remove_scene",
    ]


def test_live_flow_rejects_plugin_instance_drift(tmp_path: Path) -> None:
    client = FakeAcceptanceClient(
        tmp_path / "acceptance.mkv", drift_at="obs_control__create_source"
    )

    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED$"):
        exercise_live_obs(
            client,
            host_pid=12345,
            platform="windows",
            architecture="x86_64",
            native_plugin_sha256=ARTIFACT_DIGEST,
            python_wheel_sha256=WHEEL_DIGEST,
            output_root=tmp_path,
            authenticated=True,
            loaded_native_artifact_verified=True,
            installed_python_wheel_verified=True,
            sleeper=lambda _seconds: None,
        )


def test_loaded_native_plugin_must_match_the_packaged_manifest(tmp_path: Path) -> None:
    binary = tmp_path / "loaded" / "dcc-mcp-obs.dll"
    binary.parent.mkdir()
    binary.write_bytes(b"native-plugin")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    archive = tmp_path / "dcc-mcp-obs-1.1.0-windows.zip"
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs",
        "version": "1.1.0",
        "platform": "windows",
        "files": [
            {
                "source": "payload/bin/64bit/dcc-mcp-obs.dll",
                "target": "bin/64bit/dcc-mcp-obs.dll",
                "sha256": digest,
            }
        ],
    }
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
        package.writestr("payload/bin/64bit/dcc-mcp-obs.dll", b"native-plugin")

    result = verify_loaded_native_plugin(archive, mapped_files=[binary])

    assert result == {
        "version": "1.1.0",
        "platform": "windows",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "binary_sha256": digest,
    }

    binary.write_bytes(b"drifted-native-plugin")
    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH$"):
        verify_loaded_native_plugin(archive, mapped_files=[binary])


def test_installed_adapter_files_must_match_the_supplied_wheel(tmp_path: Path) -> None:
    package_root = tmp_path / "site-packages" / "dcc_mcp_obs"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_bytes(b'VALUE = "installed"\n')
    wheel = tmp_path / "dcc_mcp_obs-1.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as package:
        package.writestr("dcc_mcp_obs/__init__.py", 'VALUE = "installed"\n')
        package.writestr(
            "dcc_mcp_obs-1.1.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: dcc-mcp-obs\nVersion: 1.1.0\n",
        )

    distribution = SimpleNamespace(
        metadata={"Name": "dcc-mcp-obs"},
        version="1.1.0",
        files=[Path("dcc_mcp_obs/__init__.py")],
        locate_file=lambda value: tmp_path / "site-packages" / value,
        read_text=lambda _name: None,
    )
    result = verify_installed_python_wheel(
        wheel, package_root=package_root, distribution_getter=lambda _name: distribution
    )

    assert result == {
        "version": "1.1.0",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }

    (package_root / "__init__.py").write_bytes(b'VALUE = "drifted"\n')
    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH$"):
        verify_installed_python_wheel(
            wheel, package_root=package_root, distribution_getter=lambda _name: distribution
        )


def test_installed_adapter_proof_rejects_editable_distribution(tmp_path: Path) -> None:
    package_root = tmp_path / "site-packages" / "dcc_mcp_obs"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text('VALUE = "installed"\n', encoding="utf-8")
    wheel = tmp_path / "dcc_mcp_obs-1.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as package:
        package.writestr("dcc_mcp_obs/__init__.py", 'VALUE = "installed"\n')
        package.writestr(
            "dcc_mcp_obs-1.1.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: dcc-mcp-obs\nVersion: 1.1.0\n",
        )
    distribution = SimpleNamespace(
        metadata={"Name": "dcc-mcp-obs"},
        version="1.1.0",
        files=[Path("dcc_mcp_obs/__init__.py")],
        locate_file=lambda value: tmp_path / "site-packages" / value,
        read_text=lambda _name: json.dumps({"dir_info": {"editable": True}}),
    )

    with pytest.raises(AcceptanceContractError, match=r"^OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH$"):
        verify_installed_python_wheel(
            wheel, package_root=package_root, distribution_getter=lambda _name: distribution
        )


class FakeLiveTransport:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.sequence = 0

    def vendor_request(
        self,
        request_type: str,
        _data: dict[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        assert request_type == "GetPluginStatus"
        assert deadline is not None
        self.sequence += 1
        return {
            "instanceId": "obs-acceptance-session",
            "pluginVersion": "1.1.0",
            "obsVersion": "32.2.1",
            "hostPid": os.getpid(),
            "eventSequence": self.sequence,
            "ready": True,
            "ok": True,
        }

    def close(self) -> None:
        pass


def test_mcp_acceptance_client_binds_every_tool_call_to_one_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DCC_MCP_GATEWAY_PORT", "0")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setattr(server, "resolve_obs_pid", lambda _pid=None: os.getpid())
    monkeypatch.setattr(server, "ObsWebSocketTransport", FakeLiveTransport)
    monkeypatch.setattr(_client, "resolve_obs_pid", lambda _pid=None: os.getpid())
    monkeypatch.setattr(_client, "ObsWebSocketTransport", FakeLiveTransport)
    instance = server.ObsMcpServer(port=0, host_pid=os.getpid())
    instance.register_builtin_actions()
    handle = instance.start()

    try:
        client = McpAcceptanceClient(
            handle.mcp_url(), session_id="obs-acceptance-session", timeout_seconds=5
        )
        client.initialize()
        first_session = client.session_id
        result = client.call("obs_control__get_status", {})

        assert first_session
        assert client.session_id == first_session
        assert result["success"] is True
        assert result["context"]["instanceId"] == "obs-acceptance-session"
    finally:
        instance.stop()


def test_native_unload_does_not_call_obs_websocket_after_frontend_exit() -> None:
    source = (Path(__file__).parents[1] / "native" / "src" / "plugin-main.cpp").read_text(
        encoding="utf-8"
    )
    frontend_event = source.split("void frontend_event(", 1)[1].split(
        "constexpr const char *kRequests[]", 1
    )[0]
    unload = source.split("void obs_module_unload(void)", 1)[1]

    assert "OBS_FRONTEND_EVENT_EXIT" in frontend_event
    assert "unregister_vendor_requests()" in frontend_event
    unload_before_log = unload.split('blog(LOG_INFO, "dcc-mcp-obs native plugin unloaded")', 1)[0]
    assert "unregister_vendor_requests()" not in unload_before_log
    assert "g_vendor = nullptr" in unload_before_log
