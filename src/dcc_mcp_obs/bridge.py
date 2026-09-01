"""Bounded typed bridge to the native OBS plugin vendor contract."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .__version__ import __version__
from .deadline import current_deadline

DEFAULT_OPERATION_TIMEOUT_SECONDS = 30.0
ALLOWLISTED_HOTKEYS = frozenset(
    {
        "start_recording",
        "stop_recording",
        "start_streaming",
        "stop_streaming",
        "start_replay_buffer",
        "stop_replay_buffer",
        "start_virtual_camera",
        "stop_virtual_camera",
        "OBSBasic.StartRecording",
        "OBSBasic.StopRecording",
        "OBSBasic.StartStreaming",
        "OBSBasic.StopStreaming",
        "OBSBasic.StartReplayBuffer",
        "OBSBasic.StopReplayBuffer",
        "OBSBasic.StartVirtualCam",
        "OBSBasic.StopVirtualCam",
    }
)
MAX_PUBLIC_ERROR_CODE_LENGTH = 64
PUBLIC_DOWNSTREAM_ERRORS = frozenset(
    {
        "OBS_ARGUMENT_INVALID",
        "OBS_AUTHENTICATION_FAILED",
        "OBS_AUTHENTICATION_REQUIRED",
        "OBS_CONNECTION_FAILED",
        "OBS_EVENT_OVERFLOW",
        "OBS_FRAME_TOO_LARGE",
        "OBS_HANDSHAKE_INVALID",
        "OBS_INSTANCE_NOT_READY",
        "OBS_RECORDING_NOT_ACTIVE",
        "OBS_REQUEST_FAILED",
        "OBS_REQUEST_INVALID",
        "OBS_RESPONSE_INVALID",
        "OBS_RESPONSE_MISMATCH",
        "OBS_SCENE_NOT_FOUND",
        "OBS_SCENE_ITEM_NOT_FOUND",
        "OBS_SOURCE_NOT_FOUND",
        "OBS_TRANSITION_NOT_FOUND",
        "OBS_STUDIO_MODE_INACTIVE",
        "OBS_OUTPUT_NOT_FOUND",
        "OBS_OUTPUT_NOT_ACTIVE",
        "OBS_OUTPUT_ACTIVE",
        "OBS_MUTATION_REJECTED",
        "OBS_TIMEOUT",
        "OBS_UI_TIMEOUT",
        "OBS_VERSION_UNSUPPORTED",
        "OBS_PROFILE_NOT_FOUND",
        "OBS_SCENE_COLLECTION_NOT_FOUND",
        "OBS_HOTKEY_NOT_ALLOWLISTED",
        "OBS_TARGET_AMBIGUOUS",
        "OBS_SCREENSHOT_INVALID",
        "OBS_SCREENSHOT_UNVERIFIED",
        "OBS_RESPONSE_INCOMPLETE",
        "OBS_UI_INDETERMINATE",
        "OBS_UNSUPPORTED_PLATFORM",
        "OBS_WINDOW_IDENTITY_DRIFT",
        "OBS_WINDOW_NOT_FOUND",
        "OBS_SCHEMA_UNSUPPORTED",
        "OBS_SOURCE_KIND_UNSUPPORTED",
        "OBS_FILTER_KIND_UNSUPPORTED",
        "OBS_PROPERTY_NOT_FOUND",
        "OBS_MEDIA_NOT_CONTROLLABLE",
    }
)
_IDENTITY_KEYS = frozenset(
    {"instanceId", "pluginVersion", "obsVersion", "hostPid", "eventSequence", "ok"}
)
WINDOW_CAPTURE_METHODS = frozenset({"automatic", "bitblt", "windows_graphics_capture"})
TYPED_SETTINGS_SCHEMA_VERSION = "1.0"
REVIEWED_INPUT_KINDS = frozenset({"color_source_v3"})
REVIEWED_FILTER_KINDS = frozenset({"gain_filter"})
SOURCE_MONITOR_TYPES = frozenset({"none", "monitor_only", "monitor_and_output"})
MEDIA_STATES = frozenset(
    {"none", "playing", "opening", "buffering", "paused", "stopped", "ended", "error"}
)
SOURCE_VOLUME_READBACK_TOLERANCE = 1e-6
MEDIA_SEEK_READBACK_TOLERANCE_MS = 250
AGENT_INPUT_OVERLAY_SOURCE_KIND = "dcc_mcp_agent_input_overlay"
AGENT_INPUT_OVERLAY_THEME = "dcc_mcp_dark"
AGENT_INPUT_OVERLAY_ANCHORS = frozenset(
    {
        "top_left",
        "top_center",
        "top_right",
        "center_left",
        "center_right",
        "bottom_left",
        "bottom_center",
        "bottom_right",
    }
)
AGENT_INPUT_OVERLAY_MIN_OPACITY = 20
AGENT_INPUT_OVERLAY_MAX_OPACITY = 100
AGENT_INPUT_OVERLAY_MIN_MARGIN = 8
AGENT_INPUT_OVERLAY_MAX_MARGIN = 160
AGENT_INPUT_EVENT_KINDS = frozenset({"shortcut", "mouse_button", "mouse_wheel", "typing"})
AGENT_INPUT_KEYS = frozenset(
    {
        "ctrl",
        "shift",
        "alt",
        "meta",
        "enter",
        "escape",
        "tab",
        "space",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
    }
    | {chr(code) for code in range(ord("a"), ord("z") + 1)}
    | {str(number) for number in range(10)}
    | {f"f{number}" for number in range(1, 13)}
)
AGENT_INPUT_MOUSE_BUTTONS = frozenset({"none", "left", "right", "middle", "back", "forward"})
AGENT_INPUT_WHEEL_DIRECTIONS = frozenset({"none", "up", "down", "left", "right"})
DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME = "DCC-MCP Agent Input"
PROGRAM_FRAME_WIDTH = 320
PROGRAM_FRAME_HEIGHT = 180
MAX_PROGRAM_FRAME_BYTES = 400_000
_PNG_DATA_URL_PREFIX = "data:image/png;base64,"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _public_error_code(raw: object, *, fallback: str) -> str:
    if (
        type(raw) is str
        and 1 <= len(raw) <= MAX_PUBLIC_ERROR_CODE_LENGTH
        and raw in PUBLIC_DOWNSTREAM_ERRORS
    ):
        return raw
    return fallback


class VendorTransport(Protocol):
    def vendor_request(
        self,
        request_type: str,
        data: Mapping[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]: ...


class BridgeError(RuntimeError):
    """Stable error envelope without downstream/private details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Identity:
    instance_id: str
    plugin_version: str
    obs_version: str
    host_pid: int


class ObsControlBridge:
    """Typed OBS calls with exact-instance and postcondition enforcement."""

    def __init__(
        self,
        transport: VendorTransport,
        *,
        expected_pid: int,
        expected_instance_id: str | None = None,
        postcondition_attempts: int = 20,
        postcondition_poll_seconds: float = 0.1,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(expected_pid, int) or isinstance(expected_pid, bool) or expected_pid <= 0:
            raise BridgeError("OBS_IDENTITY_INVALID")
        if (
            not isinstance(postcondition_attempts, int)
            or isinstance(postcondition_attempts, bool)
            or not 1 <= postcondition_attempts <= 20
            or not 0 <= postcondition_poll_seconds <= 1
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        self._transport = transport
        self._postcondition_attempts = postcondition_attempts
        self._postcondition_poll_seconds = postcondition_poll_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._request_lock = threading.Lock()
        if deadline is not None and (
            not isinstance(deadline, (int, float))
            or isinstance(deadline, bool)
            or not math.isfinite(deadline)
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        self._bound_deadline = deadline if deadline is not None else current_deadline()
        operation_deadline = self._operation_deadline()
        try:
            status = self._request("GetPluginStatus", deadline=operation_deadline)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("OBS_CONNECTION_FAILED") from None
        self._identity = self._parse_identity(status)
        self._event_sequence = self._parse_event_sequence(status)
        self._validate_read_only_response("GetPluginStatus", status)
        if (
            self._identity.host_pid != expected_pid
            or (
                expected_instance_id is not None
                and self._identity.instance_id != expected_instance_id
            )
            or status.get("ready") is not True
        ):
            raise BridgeError("OBS_INSTANCE_NOT_READY")
        if self._identity.plugin_version != __version__:
            raise BridgeError("OBS_PLUGIN_VERSION_UNSUPPORTED")
        try:
            obs_major = int(self._identity.obs_version.split(".", 1)[0])
        except (ValueError, TypeError):
            raise BridgeError("OBS_VERSION_UNSUPPORTED") from None
        if obs_major < 28:
            raise BridgeError("OBS_VERSION_UNSUPPORTED")

    def status(self) -> dict[str, object]:
        return self._checked("GetPluginStatus", deadline=self._operation_deadline())

    def list_scenes(self) -> dict[str, object]:
        return self._checked("ListScenes", deadline=self._operation_deadline())

    # Scene graph operations are deliberately typed wrappers.  Callers can
    # only address bounded names/ids and every mutation is followed by a
    # separate native readback on the same, identity-bound transport.
    def get_current_scene(self) -> dict[str, object]:
        return self._checked("GetCurrentScene", deadline=self._operation_deadline())

    def set_current_scene(self, scene_name: str) -> dict[str, object]:
        self._select_exact_name(
            self.list_scenes(),
            "scenes",
            "sceneName",
            scene_name,
            missing_code="OBS_SCENE_NOT_FOUND",
        )
        return self._scene_name_mutation(
            "SetCurrentScene", "GetCurrentScene", scene_name, capability="scene_switch"
        )

    switch_scene = set_current_scene

    def create_scene(self, scene_name: str) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        deadline = self._operation_deadline()
        accepted = self._checked(
            "CreateScene", {"sceneName": scene_name, "capability": "scene_graph"}, deadline=deadline
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            scenes = self._checked("ListScenes", deadline=deadline)
            if any(item.get("sceneName") == scene_name for item in scenes.get("scenes", [])):
                return {**scenes, "sceneName": scene_name, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def remove_scene(self, scene_name: str) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        self._select_exact_name(
            self.list_scenes(),
            "scenes",
            "sceneName",
            scene_name,
            missing_code="OBS_SCENE_NOT_FOUND",
        )
        deadline = self._operation_deadline()
        accepted = self._checked(
            "RemoveScene", {"sceneName": scene_name, "capability": "scene_graph"}, deadline=deadline
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            scenes = self._checked("ListScenes", deadline=deadline)
            if not any(item.get("sceneName") == scene_name for item in scenes.get("scenes", [])):
                return {**scenes, "removed": True, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def rename_scene(self, scene_name: str, new_scene_name: str) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        new_scene_name = self._require_name(new_scene_name)
        if scene_name == new_scene_name:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        scenes = self.list_scenes()
        self._select_exact_name(
            scenes, "scenes", "sceneName", scene_name, missing_code="OBS_SCENE_NOT_FOUND"
        )
        if any(
            isinstance(item, dict) and item.get("sceneName") == new_scene_name
            for item in scenes.get("scenes", [])
        ):
            raise BridgeError("OBS_TARGET_AMBIGUOUS")
        deadline = self._operation_deadline()
        accepted = self._checked(
            "RenameScene",
            {"sceneName": scene_name, "newSceneName": new_scene_name, "capability": "scene_graph"},
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            scenes = self._checked("ListScenes", deadline=deadline)
            names = {item.get("sceneName") for item in scenes.get("scenes", [])}
            if new_scene_name in names and scene_name not in names:
                return {**scenes, "sceneName": new_scene_name, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def list_scene_items(self, *, scene_name: str) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        return self._checked(
            "ListSceneItems", {"sceneName": scene_name}, deadline=self._operation_deadline()
        )

    def get_scene_item(self, *, scene_name: str, scene_item_id: int) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        scene_item_id = self._require_item_id(scene_item_id)
        return self._checked(
            "GetSceneItem",
            {"sceneName": scene_name, "sceneItemId": scene_item_id},
            deadline=self._operation_deadline(),
        )

    def create_scene_item(
        self,
        *,
        scene_name: str,
        source_name: str,
        source_kind: str | None = None,
        enabled: bool = True,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        if source_kind is not None:
            source_kind = self._require_name(source_kind)
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        data: dict[str, object] = {
            "sceneName": scene_name,
            "sourceName": source_name,
            "enabled": enabled,
            "capability": "scene_graph",
        }
        if source_kind is not None:
            data["sourceKind"] = source_kind
        deadline = self._operation_deadline()
        accepted = self._checked("CreateSceneItem", data, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        item_id = accepted.get("sceneItemId")
        if type(item_id) is not int or isinstance(item_id, bool) or item_id <= 0:
            raise BridgeError("OBS_RESPONSE_INVALID")
        return self._readback_scene_item(
            scene_name,
            item_id,
            deadline=deadline,
            expected_source=source_name,
            expected_kind=source_kind,
            expected_enabled=enabled,
        )

    def create_window_capture_source(
        self,
        *,
        scene_name: str,
        source_name: str,
        process_id: int,
        window_handle: int,
        window_title: str,
        capture_cursor: bool = True,
        client_area: bool = True,
        capture_method: str = "automatic",
        enabled: bool = True,
    ) -> dict[str, object]:
        payload = self._window_capture_payload(
            scene_name=scene_name,
            source_name=source_name,
            process_id=process_id,
            window_handle=window_handle,
            window_title=window_title,
            capture_cursor=capture_cursor,
            client_area=client_area,
            capture_method=capture_method,
            enabled=enabled,
        )
        deadline = self._operation_deadline()
        accepted = self._checked("CreateWindowCaptureSource", payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return self._readback_window_capture(payload, deadline=deadline)

    def list_window_capture_candidates(
        self, *, executable: str, window_title: str | None = None
    ) -> dict[str, object]:
        if (
            type(executable) is not str
            or not 1 <= len(executable) <= 256
            or any(separator in executable for separator in ("/", "\\", ":"))
            or (
                window_title is not None
                and (type(window_title) is not str or not 1 <= len(window_title) <= 256)
            )
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        payload: dict[str, object] = {"executable": executable}
        if window_title is not None:
            payload["windowTitle"] = window_title
        result = self._checked(
            "ListWindowCaptureCandidates", payload, deadline=self._operation_deadline()
        )
        candidates = result.get("candidates")
        if (
            result.get("executable", "").casefold() != executable.casefold()
            or result.get("windowTitle") != window_title
            or not isinstance(candidates, list)
            or any(
                candidate.get("executable", "").casefold() != executable.casefold()
                or (window_title is not None and candidate.get("windowTitle") != window_title)
                for candidate in candidates
            )
        ):
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        return result

    def restore_window_capture_candidate(
        self,
        *,
        executable: str,
        process_id: int,
        window_handle: int,
        window_title: str,
    ) -> dict[str, object]:
        if (
            type(executable) is not str
            or not 1 <= len(executable) <= 256
            or any(separator in executable for separator in ("/", "\\", ":"))
            or type(process_id) is not int
            or not 1 <= process_id < 2**32
            or type(window_handle) is not int
            or not 1 <= window_handle < 2**63
            or type(window_title) is not str
            or not 1 <= len(window_title) <= 256
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        payload: dict[str, object] = {
            "executable": executable,
            "processId": process_id,
            "windowHandle": window_handle,
            "windowTitle": window_title,
            "capability": "window_capture",
        }
        deadline = self._operation_deadline()
        accepted = self._checked("RestoreWindowCaptureCandidate", payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        query = {"executable": executable, "windowTitle": window_title}
        for attempt in range(self._postcondition_attempts):
            readback = self._checked("ListWindowCaptureCandidates", query, deadline=deadline)
            matches = [
                candidate
                for candidate in readback["candidates"]
                if candidate.get("processId") == process_id
                and candidate.get("windowHandle") == window_handle
                and candidate.get("windowTitle") == window_title
                and candidate.get("executable", "").casefold() == executable.casefold()
            ]
            if len(matches) == 1 and matches[0].get("captureReady") is True:
                identity = {key: readback[key] for key in _IDENTITY_KEYS}
                return {**identity, **matches[0], "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def get_window_capture_source(
        self,
        *,
        scene_name: str,
        source_name: str,
        process_id: int,
        window_handle: int,
        window_title: str,
        capture_cursor: bool = True,
        client_area: bool = True,
        capture_method: str = "automatic",
        enabled: bool = True,
    ) -> dict[str, object]:
        payload = self._window_capture_payload(
            scene_name=scene_name,
            source_name=source_name,
            process_id=process_id,
            window_handle=window_handle,
            window_title=window_title,
            capture_cursor=capture_cursor,
            client_area=client_area,
            capture_method=capture_method,
            enabled=enabled,
        )
        return self._readback_window_capture(payload, deadline=self._operation_deadline())

    def rebind_window_capture_source(
        self,
        *,
        scene_name: str,
        source_name: str,
        expected_process_id: int,
        expected_window_handle: int,
        expected_window_title: str,
        process_id: int,
        window_handle: int,
        window_title: str,
        capture_cursor: bool = True,
        client_area: bool = True,
        capture_method: str = "automatic",
        enabled: bool = True,
    ) -> dict[str, object]:
        expected = self._window_capture_payload(
            scene_name=scene_name,
            source_name=source_name,
            process_id=expected_process_id,
            window_handle=expected_window_handle,
            window_title=expected_window_title,
            capture_cursor=capture_cursor,
            client_area=client_area,
            capture_method=capture_method,
            enabled=enabled,
        )
        payload = self._window_capture_payload(
            scene_name=scene_name,
            source_name=source_name,
            process_id=process_id,
            window_handle=window_handle,
            window_title=window_title,
            capture_cursor=capture_cursor,
            client_area=client_area,
            capture_method=capture_method,
            enabled=enabled,
        )
        payload.update(
            {
                "expectedProcessId": expected["processId"],
                "expectedWindowHandle": expected["windowHandle"],
                "expectedWindowTitle": expected["windowTitle"],
            }
        )
        deadline = self._operation_deadline()
        accepted = self._checked("RebindWindowCaptureSource", payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback_payload = {
            key: value for key, value in payload.items() if not key.startswith("expected")
        }
        return self._readback_window_capture(readback_payload, deadline=deadline)

    def set_window_capture_method(
        self,
        *,
        scene_name: str,
        source_name: str,
        process_id: int,
        window_handle: int,
        window_title: str,
        capture_cursor: bool = True,
        client_area: bool = True,
        capture_method: str,
        enabled: bool = True,
    ) -> dict[str, object]:
        payload = self._window_capture_payload(
            scene_name=scene_name,
            source_name=source_name,
            process_id=process_id,
            window_handle=window_handle,
            window_title=window_title,
            capture_cursor=capture_cursor,
            client_area=client_area,
            capture_method=capture_method,
            enabled=enabled,
        )
        deadline = self._operation_deadline()
        accepted = self._checked("SetWindowCaptureMethod", payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return self._readback_window_capture(payload, deadline=deadline)

    def update_scene_item(
        self,
        *,
        scene_name: str,
        scene_item_id: int,
        enabled: bool | None = None,
    ) -> dict[str, object]:
        if enabled is None:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self.set_scene_item_enabled(
            scene_name=scene_name, scene_item_id=scene_item_id, enabled=enabled
        )

    def set_scene_item_enabled(
        self, *, scene_name: str, scene_item_id: int, enabled: bool
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        scene_item_id = self._require_item_id(scene_item_id)
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        accepted = self._checked(
            "SetSceneItemEnabled",
            {
                "sceneName": scene_name,
                "sceneItemId": scene_item_id,
                "enabled": enabled,
                "capability": "scene_graph",
            },
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return self._readback_scene_item(
            scene_name, scene_item_id, deadline=deadline, expected_enabled=enabled
        )

    def set_scene_item_transform(
        self,
        *,
        scene_name: str,
        scene_item_id: int,
        position: tuple[float, float] | None = None,
        scale: tuple[float, float] | None = None,
        rotation: float | None = None,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        scene_item_id = self._require_item_id(scene_item_id)
        data: dict[str, object] = {
            "sceneName": scene_name,
            "sceneItemId": scene_item_id,
            "capability": "scene_graph",
        }
        for key, value in (("position", position), ("scale", scale)):
            if value is not None:
                if (
                    not isinstance(value, (tuple, list))
                    or len(value) != 2
                    or any(type(component) not in (int, float) for component in value)
                ):
                    raise BridgeError("OBS_ARGUMENT_INVALID")
                prefix = "pos" if key == "position" else "scale"
                data[f"{prefix}X"] = float(value[0])
                data[f"{prefix}Y"] = float(value[1])
        if rotation is not None:
            if type(rotation) not in (int, float) or not math.isfinite(float(rotation)):
                raise BridgeError("OBS_ARGUMENT_INVALID")
            data["rotation"] = float(rotation)
        if len(data) == 3:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        accepted = self._checked("SetSceneItemTransform", data, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return self._readback_scene_item(
            scene_name,
            scene_item_id,
            deadline=deadline,
            expected_pos=(float(position[0]), float(position[1])) if position is not None else None,
            expected_scale=(float(scale[0]), float(scale[1])) if scale is not None else None,
            expected_rotation=float(rotation) if rotation is not None else None,
        )

    def remove_scene_item(self, *, scene_name: str, scene_item_id: int) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        scene_item_id = self._require_item_id(scene_item_id)
        deadline = self._operation_deadline()
        accepted = self._checked(
            "RemoveSceneItem",
            {"sceneName": scene_name, "sceneItemId": scene_item_id, "capability": "scene_graph"},
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            try:
                readback = self._checked(
                    "GetSceneItem",
                    {"sceneName": scene_name, "sceneItemId": scene_item_id},
                    deadline=deadline,
                )
            except BridgeError as exc:
                if exc.code == "OBS_SCENE_ITEM_NOT_FOUND":
                    return {
                        "sceneName": scene_name,
                        "sceneItemId": scene_item_id,
                        "removed": True,
                        "verified": True,
                    }
                raise
            if readback.get("exists") is False or readback.get("removed") is True:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def list_transitions(self) -> dict[str, object]:
        return self._checked("ListTransitions", deadline=self._operation_deadline())

    def get_current_transition(self) -> dict[str, object]:
        return self._checked("GetCurrentTransition", deadline=self._operation_deadline())

    def set_current_transition(
        self, transition_name: str, *, duration_ms: int | None = None
    ) -> dict[str, object]:
        transition_name = self._require_name(transition_name)
        if duration_ms is not None and (
            type(duration_ms) is not int or duration_ms < 0 or duration_ms > 3_600_000
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        data: dict[str, object] = {"transitionName": transition_name, "capability": "transitions"}
        if duration_ms is not None:
            data["durationMs"] = duration_ms
        return self._name_mutation(
            "SetCurrentTransition", "GetCurrentTransition", "transitionName", transition_name, data
        )

    def trigger_transition(self, scene_name: str) -> dict[str, object]:
        self._select_exact_name(
            self.list_scenes(),
            "scenes",
            "sceneName",
            scene_name,
            missing_code="OBS_SCENE_NOT_FOUND",
        )
        deadline = self._operation_deadline()
        accepted = self._checked(
            "TriggerTransition",
            {"sceneName": scene_name, "capability": "transitions"},
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return {
            **self._checked("GetCurrentTransition", deadline=deadline),
            "sceneName": scene_name,
            "accepted": True,
            "verified": True,
        }

    def get_studio_mode_status(self) -> dict[str, object]:
        return self._checked("GetStudioModeStatus", deadline=self._operation_deadline())

    get_studio_mode = get_studio_mode_status

    def set_studio_mode(self, enabled: bool) -> dict[str, object]:
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        accepted = self._checked(
            "SetStudioMode",
            {"studioModeEnabled": enabled, "capability": "studio_mode"},
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            status = self._checked("GetStudioModeStatus", deadline=deadline)
            if status.get("studioModeEnabled") is enabled:
                return {**status, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def get_current_preview_scene(self) -> dict[str, object]:
        return self._checked("GetCurrentPreviewScene", deadline=self._operation_deadline())

    get_preview_scene = get_current_preview_scene

    def set_current_preview_scene(self, scene_name: str) -> dict[str, object]:
        self._select_exact_name(
            self.list_scenes(),
            "scenes",
            "sceneName",
            scene_name,
            missing_code="OBS_SCENE_NOT_FOUND",
        )
        return self._scene_name_mutation(
            "SetCurrentPreviewScene",
            "GetCurrentPreviewScene",
            scene_name,
            capability="studio_preview",
        )

    set_preview_scene = set_current_preview_scene

    def trigger_studio_mode_transition(self) -> dict[str, object]:
        deadline = self._operation_deadline()
        before = self._checked("GetStudioModeStatus", deadline=deadline)
        before_program = before.get("programSceneName")
        before_preview = before.get("previewSceneName")
        if not isinstance(before_program, str) or not isinstance(before_preview, str):
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        accepted = self._checked(
            "TriggerStudioModeTransition", {"capability": "studio_transition"}, deadline=deadline
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        # An accepted acknowledgement alone can describe a no-op.  Require the
        # native Studio Mode readback to prove the preview/program swap.
        for attempt in range(self._postcondition_attempts):
            status = self._checked("GetStudioModeStatus", deadline=deadline)
            if (
                status.get("programSceneName") == before_preview
                and status.get("previewSceneName") == before_program
            ):
                return {**status, "accepted": True, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    transition_to_program = trigger_studio_mode_transition

    # Profiles and scene collections are deliberately name-based only after a
    # bounded discovery call.  This prevents a stale or ambiguous operator
    # target from being silently selected by the native side.
    def list_profiles(self) -> dict[str, object]:
        return self._checked("ListProfiles", deadline=self._operation_deadline())

    def get_current_profile(self) -> dict[str, object]:
        return self._checked("GetCurrentProfile", deadline=self._operation_deadline())

    def set_current_profile(self, profile_name: str) -> dict[str, object]:
        self._select_exact_name(
            self.list_profiles(),
            "profiles",
            "profileName",
            profile_name,
            missing_code="OBS_PROFILE_NOT_FOUND",
        )
        return self._select_profile_or_collection(
            "SetCurrentProfile", "GetCurrentProfile", "profileName", profile_name
        )

    def list_scene_collections(self) -> dict[str, object]:
        return self._checked("ListSceneCollections", deadline=self._operation_deadline())

    def get_current_scene_collection(self) -> dict[str, object]:
        return self._checked("GetCurrentSceneCollection", deadline=self._operation_deadline())

    def set_current_scene_collection(self, collection_name: str) -> dict[str, object]:
        self._select_exact_name(
            self.list_scene_collections(),
            "sceneCollections",
            "sceneCollectionName",
            collection_name,
            missing_code="OBS_SCENE_COLLECTION_NOT_FOUND",
        )
        return self._select_profile_or_collection(
            "SetCurrentSceneCollection",
            "GetCurrentSceneCollection",
            "sceneCollectionName",
            collection_name,
        )

    def list_allowlisted_hotkeys(self) -> dict[str, object]:
        return self._checked("ListAllowlistedHotkeys", deadline=self._operation_deadline())

    def trigger_allowlisted_hotkey(self, hotkey_id: str) -> dict[str, object]:
        if (
            not isinstance(hotkey_id, str)
            or not 1 <= len(hotkey_id) <= 128
            or hotkey_id not in ALLOWLISTED_HOTKEYS
        ):
            raise BridgeError("OBS_HOTKEY_NOT_ALLOWLISTED")
        # The native request is intentionally named *allowlisted*: it accepts
        # only operator-configured identifiers and never arbitrary key input.
        response = self._checked(
            "TriggerAllowlistedHotkey",
            {"hotkeyName": hotkey_id},
            deadline=self._operation_deadline(),
        )
        if response.get("accepted") is not True:
            raise BridgeError("OBS_HOTKEY_NOT_ALLOWLISTED")
        if response.get("hotkeyName") != hotkey_id:
            raise BridgeError("OBS_RESPONSE_INVALID")
        return {**response, "hotkeyName": hotkey_id}

    def capture_source_screenshot(
        self, source_name: str, *, image_format: str = "png"
    ) -> dict[str, object]:
        if (
            not isinstance(source_name, str)
            or not 1 <= len(source_name) <= 256
            or not isinstance(image_format, str)
            or image_format not in {"png", "jpg", "jpeg", "webp"}
        ):
            raise BridgeError("OBS_SCREENSHOT_INVALID")
        self._checked(
            "CaptureScreenshot",
            {"sourceName": source_name, "imageFormat": image_format},
            deadline=self._operation_deadline(),
        )
        # The OBS frontend API is fire-and-forget and exposes no completion or
        # artifact readback contract.  Never claim a screenshot was captured.
        raise BridgeError("OBS_SCREENSHOT_UNVERIFIED")

    def capture_program_frame(self) -> dict[str, object]:
        response = self._checked(
            "CaptureProgramFrame",
            {
                "imageFormat": "png",
                "imageWidth": PROGRAM_FRAME_WIDTH,
                "imageHeight": PROGRAM_FRAME_HEIGHT,
            },
            deadline=self._operation_deadline(),
        )
        image = self._decode_program_frame(response)
        return {
            **response,
            "imageWidth": PROGRAM_FRAME_WIDTH,
            "imageHeight": PROGRAM_FRAME_HEIGHT,
            "byteLength": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        }

    def get_operator_status(self) -> dict[str, object]:
        return self._checked("GetOperatorStatus", deadline=self._operation_deadline())

    operator_status = get_operator_status

    @staticmethod
    def _require_name(value: str) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return value

    @staticmethod
    def _require_item_id(value: int) -> int:
        if type(value) is not int or value <= 0:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return value

    def _window_capture_payload(
        self,
        *,
        scene_name: str,
        source_name: str,
        process_id: int,
        window_handle: int,
        window_title: str,
        capture_cursor: bool,
        client_area: bool,
        capture_method: str,
        enabled: bool,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        window_title = self._require_name(window_title)
        if type(process_id) is not int or not 1 <= process_id < 2**32:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        if type(window_handle) is not int or not 1 <= window_handle < 2**63:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        if any(type(value) is not bool for value in (capture_cursor, client_area, enabled)):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        if type(capture_method) is not str or capture_method not in WINDOW_CAPTURE_METHODS:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return {
            "sceneName": scene_name,
            "sourceName": source_name,
            "processId": process_id,
            "windowHandle": window_handle,
            "windowTitle": window_title,
            "captureCursor": capture_cursor,
            "clientArea": client_area,
            "captureMethod": capture_method,
            "enabled": enabled,
            "capability": "window_capture",
        }

    def _readback_window_capture(
        self, expected: Mapping[str, object], *, deadline: float
    ) -> dict[str, object]:
        for attempt in range(self._postcondition_attempts):
            readback = self._checked("GetWindowCaptureSource", expected, deadline=deadline)
            if (
                readback.get("bindingVerified") is True
                and readback.get("sourceKind") == "window_capture"
                and all(
                    readback.get(response_key) == expected[request_key]
                    for request_key, response_key in (
                        ("sceneName", "sceneName"),
                        ("sourceName", "sourceName"),
                        ("processId", "processId"),
                        ("windowHandle", "windowHandle"),
                        ("windowTitle", "windowTitle"),
                        ("captureCursor", "captureCursor"),
                        ("clientArea", "clientArea"),
                        ("captureMethod", "captureMethod"),
                        ("enabled", "enabled"),
                    )
                )
            ):
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _poll(self, deadline: float) -> None:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise BridgeError("OBS_TIMEOUT")
        self._sleeper(min(self._postcondition_poll_seconds, remaining))

    def _scene_name_mutation(
        self, request_type: str, status_request: str, scene_name: str, *, capability: str
    ) -> dict[str, object]:
        return self._name_mutation(
            request_type,
            status_request,
            "sceneName",
            scene_name,
            {"sceneName": scene_name, "capability": capability},
        )

    def _name_mutation(
        self,
        request_type: str,
        status_request: str,
        field: str,
        target: str,
        data: Mapping[str, object],
    ) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, data, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(status_request, deadline=deadline)
            if readback.get(field) == target:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _readback_scene_item(
        self,
        scene_name: str,
        scene_item_id: int,
        *,
        deadline: float,
        expected_source: str | None = None,
        expected_kind: str | None = None,
        expected_enabled: bool | None = None,
        expected_pos: tuple[float, float] | None = None,
        expected_scale: tuple[float, float] | None = None,
        expected_rotation: float | None = None,
    ) -> dict[str, object]:
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(
                "GetSceneItem",
                {"sceneName": scene_name, "sceneItemId": scene_item_id},
                deadline=deadline,
            )
            valid = (
                readback.get("sceneName") == scene_name
                and readback.get("sceneItemId") == scene_item_id
                and (expected_source is None or readback.get("sourceName") == expected_source)
                and (expected_kind is None or readback.get("sourceKind") == expected_kind)
                and (expected_enabled is None or readback.get("enabled") is expected_enabled)
                and (
                    expected_pos is None
                    or (
                        isinstance(readback.get("posX"), (int, float))
                        and not isinstance(readback.get("posX"), bool)
                        and isinstance(readback.get("posY"), (int, float))
                        and not isinstance(readback.get("posY"), bool)
                        and math.isclose(float(readback["posX"]), expected_pos[0], abs_tol=1e-3)
                        and math.isclose(float(readback["posY"]), expected_pos[1], abs_tol=1e-3)
                    )
                )
                and (
                    expected_scale is None
                    or (
                        isinstance(readback.get("scaleX"), (int, float))
                        and not isinstance(readback.get("scaleX"), bool)
                        and isinstance(readback.get("scaleY"), (int, float))
                        and not isinstance(readback.get("scaleY"), bool)
                        and math.isclose(float(readback["scaleX"]), expected_scale[0], abs_tol=1e-3)
                        and math.isclose(float(readback["scaleY"]), expected_scale[1], abs_tol=1e-3)
                    )
                )
                and (
                    expected_rotation is None
                    or (
                        isinstance(readback.get("rotation"), (int, float))
                        and not isinstance(readback.get("rotation"), bool)
                        and math.isclose(
                            float(readback["rotation"]), expected_rotation, abs_tol=1e-3
                        )
                    )
                )
            )
            if valid:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    @staticmethod
    def _select_exact_name(
        response: Mapping[str, object],
        collection_key: str,
        name_key: str,
        target: str,
        *,
        missing_code: str,
    ) -> None:
        if not isinstance(target, str) or not 1 <= len(target) <= 256:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        entries = response.get(collection_key)
        if not isinstance(entries, list):
            raise BridgeError("OBS_RESPONSE_INVALID")
        if response.get("truncated") is not False:
            raise BridgeError("OBS_RESPONSE_INCOMPLETE")
        matches = [
            entry for entry in entries if isinstance(entry, dict) and entry.get(name_key) == target
        ]
        if len(matches) > 1:
            raise BridgeError("OBS_TARGET_AMBIGUOUS")
        if not matches:
            raise BridgeError(missing_code)

    def _select_profile_or_collection(
        self, request_type: str, status_request: str, field: str, target: str
    ) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, {field: target}, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(status_request, deadline=deadline)
            if readback.get(field) == target:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def list_sources(self, *, scene_name: str | None = None) -> dict[str, object]:
        data: dict[str, object] = {}
        if scene_name is not None:
            if not isinstance(scene_name, str) or not scene_name or len(scene_name) > 256:
                raise BridgeError("OBS_ARGUMENT_INVALID")
            data["sceneName"] = scene_name
        return self._checked("ListSources", data, deadline=self._operation_deadline())

    def recording_status(self) -> dict[str, object]:
        return self._checked("GetRecordingStatus", deadline=self._operation_deadline())

    def start_recording(self) -> dict[str, object]:
        return self._recording_mutation("StartRecording", active=True, paused=False)

    def stop_recording(self) -> dict[str, object]:
        return self._recording_mutation("StopRecording", active=False, paused=False)

    def request_graceful_shutdown(self) -> dict[str, object]:
        """Ask OBS to exit after the typed response has been submitted.

        This is intentionally a terminal operation.  Process disappearance is
        verified by the caller rather than claimed as an in-band postcondition.
        """
        response = self._checked(
            "RequestGracefulShutdown",
            {"capability": "application_lifecycle"},
            deadline=self._operation_deadline(),
        )
        if response.get("accepted") is not True or response.get("shutdownScheduled") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return response

    def create_agent_input_overlay(
        self,
        *,
        scene_name: str,
        source_name: str = DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME,
        anchor: str = "bottom_right",
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        if anchor not in AGENT_INPUT_OVERLAY_ANCHORS:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        self._select_exact_name(
            self.list_scenes(),
            "scenes",
            "sceneName",
            scene_name,
            missing_code="OBS_SCENE_NOT_FOUND",
        )
        deadline = self._operation_deadline()
        accepted = self._checked(
            "CreateAgentInputOverlay",
            {
                "sceneName": scene_name,
                "sourceName": source_name,
                "anchor": anchor,
                "capability": "agent_input_overlay",
            },
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback = self._get_agent_input_overlay(
            scene_name=scene_name, source_name=source_name, deadline=deadline
        )
        if readback.get("anchor") != anchor:
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        return {**readback, "verified": True}

    def get_agent_input_overlay(
        self,
        *,
        scene_name: str,
        source_name: str = DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME,
    ) -> dict[str, object]:
        return self._get_agent_input_overlay(
            scene_name=self._require_name(scene_name),
            source_name=self._require_name(source_name),
            deadline=self._operation_deadline(),
        )

    def _get_agent_input_overlay(
        self, *, scene_name: str, source_name: str, deadline: float
    ) -> dict[str, object]:
        response = self._checked(
            "GetAgentInputOverlay",
            {"sceneName": scene_name, "sourceName": source_name},
            deadline=deadline,
        )
        if response.get("sceneName") != scene_name or response.get("sourceName") != source_name:
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        keys_csv = str(response["keysCsv"])
        public_response = {key: value for key, value in response.items() if key != "keysCsv"}
        return {**public_response, "keys": keys_csv.split(",") if keys_csv else []}

    def set_agent_input_overlay_layout(
        self,
        *,
        scene_name: str,
        anchor: str,
        opacity: int,
        margin: int,
        source_name: str = DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        if (
            anchor not in AGENT_INPUT_OVERLAY_ANCHORS
            or type(opacity) is not int
            or not AGENT_INPUT_OVERLAY_MIN_OPACITY <= opacity <= AGENT_INPUT_OVERLAY_MAX_OPACITY
            or type(margin) is not int
            or not AGENT_INPUT_OVERLAY_MIN_MARGIN <= margin <= AGENT_INPUT_OVERLAY_MAX_MARGIN
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        accepted = self._checked(
            "SetAgentInputOverlayLayout",
            {
                "sceneName": scene_name,
                "sourceName": source_name,
                "anchor": anchor,
                "opacity": opacity,
                "margin": margin,
                "capability": "agent_input_overlay",
            },
            deadline=deadline,
        )
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback = self._get_agent_input_overlay(
            scene_name=scene_name, source_name=source_name, deadline=deadline
        )
        if (
            readback.get("anchor") != anchor
            or readback.get("opacity") != opacity
            or readback.get("margin") != margin
        ):
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        return {**readback, "verified": True}

    def emit_agent_input_activity(
        self,
        *,
        scene_name: str,
        event_kind: str,
        source_name: str = DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME,
        keys: list[str] | None = None,
        mouse_button: str = "none",
        wheel_direction: str = "none",
        character_count: int = 0,
        duration_ms: int = 1600,
        agent_id: str = "agent",
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        if keys is not None and not isinstance(keys, list):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        normalized_keys = list(keys or [])
        if (
            event_kind not in AGENT_INPUT_EVENT_KINDS
            or type(duration_ms) is not int
            or not 250 <= duration_ms <= 5000
            or mouse_button not in AGENT_INPUT_MOUSE_BUTTONS
            or wheel_direction not in AGENT_INPUT_WHEEL_DIRECTIONS
            or not isinstance(character_count, int)
            or isinstance(character_count, bool)
            or not 0 <= character_count <= 10000
            or not 0 <= len(normalized_keys) <= 4
            or any(type(key) is not str or key not in AGENT_INPUT_KEYS for key in normalized_keys)
            or type(agent_id) is not str
            or not 1 <= len(agent_id) <= 64
            or any(ord(character) < 32 or ord(character) == 127 for character in agent_id)
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        if (
            (event_kind == "shortcut" and not normalized_keys)
            or (event_kind != "shortcut" and normalized_keys)
            or (event_kind == "mouse_button" and mouse_button == "none")
            or (event_kind != "mouse_button" and mouse_button != "none")
            or (event_kind == "mouse_wheel" and wheel_direction == "none")
            or (event_kind != "mouse_wheel" and wheel_direction != "none")
            or (event_kind == "typing" and character_count == 0)
            or (event_kind != "typing" and character_count != 0)
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        request = {
            "sceneName": scene_name,
            "sourceName": source_name,
            "eventKind": event_kind,
            "keysCsv": ",".join(normalized_keys),
            "mouseButton": mouse_button,
            "wheelDirection": wheel_direction,
            "characterCount": character_count,
            "durationMs": duration_ms,
            "agentId": agent_id,
            "capability": "agent_input_overlay",
        }
        accepted = self._checked("EmitAgentInputActivity", request, deadline=deadline)
        activity_sequence = accepted.get("activitySequence")
        if accepted.get("accepted") is not True or type(activity_sequence) is not int:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback = self._get_agent_input_overlay(
            scene_name=scene_name, source_name=source_name, deadline=deadline
        )
        if (
            readback.get("active") is not True
            or readback.get("activitySequence") != activity_sequence
            or readback.get("eventKind") != event_kind
            or readback.get("keys") != normalized_keys
            or readback.get("mouseButton") != mouse_button
            or readback.get("wheelDirection") != wheel_direction
            or readback.get("characterCount") != character_count
            or readback.get("durationMs") != duration_ms
            or readback.get("agentId") != agent_id
            or not 0 < readback.get("remainingMs", 0) <= duration_ms
        ):
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        return {**readback, "verified": True}

    def clear_agent_input_overlay(
        self,
        *,
        scene_name: str,
        source_name: str = DEFAULT_AGENT_INPUT_OVERLAY_SOURCE_NAME,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        deadline = self._operation_deadline()
        accepted = self._checked(
            "ClearAgentInputOverlay",
            {
                "sceneName": scene_name,
                "sourceName": source_name,
                "capability": "agent_input_overlay",
            },
            deadline=deadline,
        )
        activity_sequence = accepted.get("activitySequence")
        if accepted.get("accepted") is not True or type(activity_sequence) is not int:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback = self._get_agent_input_overlay(
            scene_name=scene_name, source_name=source_name, deadline=deadline
        )
        if (
            readback.get("active") is not False
            or readback.get("activitySequence") != activity_sequence
            or readback.get("eventKind") != "none"
            or readback.get("keys") != []
            or readback.get("mouseButton") != "none"
            or readback.get("wheelDirection") != "none"
            or readback.get("characterCount") != 0
            or readback.get("cueLabel") != ""
            or readback.get("durationMs") != 0
            or readback.get("remainingMs") != 0
        ):
            raise BridgeError("OBS_POSTCONDITION_FAILED")
        return {**readback, "verified": True}

    def pause_recording(self) -> dict[str, object]:
        return self._recording_mutation("PauseRecording", active=True, paused=True)

    def resume_recording(self) -> dict[str, object]:
        return self._recording_mutation("ResumeRecording", active=True, paused=False)

    # The following domains intentionally expose only the small typed contract
    # implemented by the native plugin.  They never forward arbitrary OBS
    # WebSocket requests or settings.
    def streaming_status(self) -> dict[str, object]:
        return self._checked("GetStreamingStatus", deadline=self._operation_deadline())

    def start_streaming(self) -> dict[str, object]:
        return self._domain_mutation(
            "StartStreaming", "GetStreamingStatus", "streamingActive", True
        )

    def stop_streaming(self) -> dict[str, object]:
        return self._domain_mutation(
            "StopStreaming", "GetStreamingStatus", "streamingActive", False
        )

    # Compatibility spellings used by the capability matrix's early draft.
    get_stream_status = streaming_status
    start_stream = start_streaming
    stop_stream = stop_streaming

    def replay_buffer_status(self) -> dict[str, object]:
        return self._checked("GetReplayBufferStatus", deadline=self._operation_deadline())

    def start_replay_buffer(self) -> dict[str, object]:
        return self._domain_mutation(
            "StartReplayBuffer", "GetReplayBufferStatus", "replayBufferActive", True
        )

    def stop_replay_buffer(self) -> dict[str, object]:
        return self._domain_mutation(
            "StopReplayBuffer", "GetReplayBufferStatus", "replayBufferActive", False
        )

    def save_replay_buffer(self) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked("SaveReplayBuffer", deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        readback = self._checked("GetReplayBufferStatus", deadline=deadline)
        # OBS accepts replay saves asynchronously and exposes no completion
        # event/file artifact in this contract.  Report submission only;
        # claiming a verified save would be a false postcondition.
        return {**readback, "accepted": True, "submitted": True}

    def virtual_camera_status(self) -> dict[str, object]:
        return self._checked("GetVirtualCameraStatus", deadline=self._operation_deadline())

    def start_virtual_camera(self) -> dict[str, object]:
        return self._domain_mutation(
            "StartVirtualCamera", "GetVirtualCameraStatus", "virtualCameraActive", True
        )

    def stop_virtual_camera(self) -> dict[str, object]:
        return self._domain_mutation(
            "StopVirtualCamera", "GetVirtualCameraStatus", "virtualCameraActive", False
        )

    def list_outputs(self) -> dict[str, object]:
        return self._checked("ListOutputs", deadline=self._operation_deadline())

    def scene_recording_status(self, *, session_id: str) -> dict[str, object]:
        session_id = self._require_session_id(session_id)
        return self._checked(
            "GetSceneRecordingSession",
            {"sessionId": session_id},
            deadline=self._operation_deadline(),
        )

    def start_scene_recordings(
        self, *, recordings: list[Mapping[str, object]]
    ) -> dict[str, object]:
        normalized = self._normalize_scene_recording_plan(recordings)
        deadline = self._operation_deadline()
        accepted = self._checked(
            "StartSceneRecordings", {"recordings": normalized}, deadline=deadline
        )
        session_id = accepted.get("sessionId")
        if accepted.get("accepted") is not True or not isinstance(session_id, str):
            raise BridgeError("OBS_MUTATION_REJECTED")
        session_id = self._require_session_id(session_id)
        expected_scenes = [item["sceneName"] for item in normalized]
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(
                "GetSceneRecordingSession", {"sessionId": session_id}, deadline=deadline
            )
            actual_scenes = [item["sceneName"] for item in readback["recordings"]]
            if (
                readback.get("sessionId") == session_id
                and readback.get("sessionActive") is True
                and actual_scenes == expected_scenes
                and all(item.get("outputActive") is True for item in readback["recordings"])
            ):
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def stop_scene_recordings(self, *, session_id: str) -> dict[str, object]:
        session_id = self._require_session_id(session_id)
        deadline = self._operation_deadline()
        accepted = self._checked(
            "StopSceneRecordings", {"sessionId": session_id}, deadline=deadline
        )
        if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(
                "GetSceneRecordingSession", {"sessionId": session_id}, deadline=deadline
            )
            if (
                readback.get("sessionId") == session_id
                and readback.get("sessionActive") is False
                and all(item.get("outputActive") is False for item in readback["recordings"])
            ):
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    @staticmethod
    def _require_session_id(session_id: object) -> str:
        if (
            type(session_id) is not str
            or not 1 <= len(session_id) <= 128
            or any(not (character.isalnum() or character in "-_") for character in session_id)
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return session_id

    @classmethod
    def _normalize_scene_recording_plan(cls, recordings: object) -> list[dict[str, str]]:
        if not isinstance(recordings, list) or not 1 <= len(recordings) <= 8:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        normalized: list[dict[str, str]] = []
        scenes: set[str] = set()
        prefixes: set[str] = set()
        invalid_filename_characters = frozenset('<>:"/\\|?*')
        for item in recordings:
            if not isinstance(item, Mapping) or set(item) != {"scene_name", "file_name_prefix"}:
                raise BridgeError("OBS_ARGUMENT_INVALID")
            scene_name = cls._require_name(item["scene_name"])
            prefix = item["file_name_prefix"]
            if (
                type(prefix) is not str
                or not 1 <= len(prefix) <= 96
                or prefix != prefix.strip()
                or prefix.endswith(".")
                or any(
                    character in invalid_filename_characters or ord(character) < 32
                    for character in prefix
                )
                or scene_name in scenes
                or prefix.casefold() in prefixes
            ):
                raise BridgeError("OBS_ARGUMENT_INVALID")
            scenes.add(scene_name)
            prefixes.add(prefix.casefold())
            normalized.append({"sceneName": scene_name, "fileNamePrefix": prefix})
        return normalized

    def output_status(self, *, output_name: str) -> dict[str, object]:
        if not isinstance(output_name, str) or not output_name or len(output_name) > 256:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._checked(
            "GetOutputStatus", {"outputName": output_name}, deadline=self._operation_deadline()
        )

    def start_output(self, *, output_name: str) -> dict[str, object]:
        return self._output_mutation("StartOutput", output_name, True)

    def stop_output(self, *, output_name: str) -> dict[str, object]:
        return self._output_mutation("StopOutput", output_name, False)

    def get_source_identity(self, *, source_name: str) -> dict[str, object]:
        source_name = self._require_name(source_name)
        return self._checked(
            "GetSourceIdentity", {"sourceName": source_name}, deadline=self._operation_deadline()
        )

    def create_source(
        self,
        *,
        scene_name: str,
        source_name: str,
        source_kind: str,
        schema_version: str,
        settings: Mapping[str, object],
        enabled: bool = True,
    ) -> dict[str, object]:
        scene_name = self._require_name(scene_name)
        source_name = self._require_name(source_name)
        normalized = self._normalize_input_settings(source_kind, schema_version, settings)
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        payload = {
            "sceneName": scene_name,
            "sourceName": source_name,
            "sourceKind": source_kind,
            "schemaVersion": schema_version,
            "settings": normalized,
            "enabled": enabled,
            "capability": "sources",
        }
        return self._mutate_and_reconcile(
            "CreateSource",
            payload,
            "GetInputSettings",
            {
                "sourceName": source_name,
                "sourceKind": source_kind,
                "schemaVersion": schema_version,
            },
            lambda result: result.get("settings") == normalized,
        )

    def rename_source(self, *, source_name: str, new_source_name: str) -> dict[str, object]:
        source_name = self._require_name(source_name)
        new_source_name = self._require_name(new_source_name)
        if source_name == new_source_name:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._mutate_and_reconcile(
            "RenameSource",
            {
                "sourceName": source_name,
                "newSourceName": new_source_name,
                "capability": "sources",
            },
            "GetSourceIdentity",
            {"sourceName": new_source_name},
            lambda result: result.get("sourceName") == new_source_name,
        )

    def remove_source(self, *, source_name: str) -> dict[str, object]:
        source_name = self._require_name(source_name)
        result = self._checked(
            "RemoveSource",
            {"sourceName": source_name, "capability": "sources"},
            deadline=self._operation_deadline(),
        )
        if result.get("accepted") is not True or result.get("removed") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return {**result, "verified": True}

    def list_input_kinds(self) -> dict[str, object]:
        return self._checked("ListInputKinds", deadline=self._operation_deadline())

    def get_input_settings(
        self, *, source_name: str, source_kind: str, schema_version: str = "1.0"
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        self._normalize_input_settings(source_kind, schema_version, {"width": 1})
        return self._checked(
            "GetInputSettings",
            {
                "sourceName": source_name,
                "sourceKind": source_kind,
                "schemaVersion": schema_version,
            },
            deadline=self._operation_deadline(),
        )

    def set_input_settings(
        self,
        *,
        source_name: str,
        source_kind: str,
        schema_version: str,
        settings: Mapping[str, object],
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        normalized = self._normalize_input_settings(source_kind, schema_version, settings)
        payload = {
            "sourceName": source_name,
            "sourceKind": source_kind,
            "schemaVersion": schema_version,
            "settings": normalized,
            "capability": "inputs",
        }
        return self._mutate_and_reconcile(
            "SetInputSettings",
            payload,
            "GetInputSettings",
            {key: value for key, value in payload.items() if key not in {"settings", "capability"}},
            lambda result: all(
                result.get("settings", {}).get(key) == value for key, value in normalized.items()
            ),
        )

    def describe_properties(
        self, *, source_kind: str, schema_version: str = "1.0"
    ) -> dict[str, object]:
        self._normalize_input_settings(source_kind, schema_version, {"width": 1})
        return self._checked(
            "DescribeProperties",
            {"sourceKind": source_kind, "schemaVersion": schema_version},
            deadline=self._operation_deadline(),
        )

    def validate_property_value(
        self,
        *,
        source_kind: str,
        schema_version: str,
        property_name: str,
        value: object,
    ) -> dict[str, object]:
        normalized = self._normalize_input_settings(
            source_kind, schema_version, {property_name: value}
        )
        return self._checked(
            "ValidatePropertyValue",
            {
                "sourceKind": source_kind,
                "schemaVersion": schema_version,
                "propertyName": property_name,
                "value": normalized[property_name],
            },
            deadline=self._operation_deadline(),
        )

    def set_property_value(
        self,
        *,
        source_name: str,
        source_kind: str,
        schema_version: str,
        property_name: str,
        value: object,
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        normalized = self._normalize_input_settings(
            source_kind, schema_version, {property_name: value}
        )
        payload = {
            "sourceName": source_name,
            "sourceKind": source_kind,
            "schemaVersion": schema_version,
            "propertyName": property_name,
            "value": normalized[property_name],
            "capability": "properties",
        }
        return self._mutate_and_reconcile(
            "SetPropertyValue",
            payload,
            "GetInputSettings",
            {
                "sourceName": source_name,
                "sourceKind": source_kind,
                "schemaVersion": schema_version,
            },
            lambda result: (
                result.get("settings", {}).get(property_name) == normalized[property_name]
            ),
        )

    def list_filters(self, *, source_name: str) -> dict[str, object]:
        return self._checked(
            "ListFilters",
            {"sourceName": self._require_name(source_name)},
            deadline=self._operation_deadline(),
        )

    def get_filter(
        self,
        *,
        source_name: str,
        filter_name: str,
        filter_kind: str,
        schema_version: str = "1.0",
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        filter_name = self._require_name(filter_name)
        self._normalize_filter_settings(filter_kind, schema_version, {"db": 0.0})
        return self._checked(
            "GetFilter",
            {
                "sourceName": source_name,
                "filterName": filter_name,
                "filterKind": filter_kind,
                "schemaVersion": schema_version,
            },
            deadline=self._operation_deadline(),
        )

    def create_filter(
        self,
        *,
        source_name: str,
        filter_name: str,
        filter_kind: str,
        schema_version: str,
        settings: Mapping[str, object],
        enabled: bool = True,
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        filter_name = self._require_name(filter_name)
        normalized = self._normalize_filter_settings(filter_kind, schema_version, settings)
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        payload = {
            "sourceName": source_name,
            "filterName": filter_name,
            "filterKind": filter_kind,
            "schemaVersion": schema_version,
            "settings": normalized,
            "enabled": enabled,
            "capability": "filters",
        }
        return self._mutate_and_reconcile(
            "CreateFilter",
            payload,
            "GetFilter",
            {
                key: value
                for key, value in payload.items()
                if key not in {"settings", "enabled", "capability"}
            },
            lambda result: (
                result.get("settings") == normalized and result.get("enabled") is enabled
            ),
        )

    def set_filter_enabled(
        self, *, source_name: str, filter_name: str, enabled: bool
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        filter_name = self._require_name(filter_name)
        if type(enabled) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._mutate_and_reconcile(
            "SetFilterEnabled",
            {
                "sourceName": source_name,
                "filterName": filter_name,
                "enabled": enabled,
                "capability": "filters",
            },
            "GetFilter",
            {"sourceName": source_name, "filterName": filter_name},
            lambda result: result.get("enabled") is enabled,
        )

    def set_filter_settings(
        self,
        *,
        source_name: str,
        filter_name: str,
        filter_kind: str,
        schema_version: str,
        settings: Mapping[str, object],
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        filter_name = self._require_name(filter_name)
        normalized = self._normalize_filter_settings(filter_kind, schema_version, settings)
        payload = {
            "sourceName": source_name,
            "filterName": filter_name,
            "filterKind": filter_kind,
            "schemaVersion": schema_version,
            "settings": normalized,
            "capability": "filters",
        }
        return self._mutate_and_reconcile(
            "SetFilterSettings",
            payload,
            "GetFilter",
            {key: value for key, value in payload.items() if key not in {"settings", "capability"}},
            lambda result: result.get("settings") == normalized,
        )

    def remove_filter(self, *, source_name: str, filter_name: str) -> dict[str, object]:
        result = self._checked(
            "RemoveFilter",
            {
                "sourceName": self._require_name(source_name),
                "filterName": self._require_name(filter_name),
                "capability": "filters",
            },
            deadline=self._operation_deadline(),
        )
        if result.get("accepted") is not True or result.get("removed") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return {**result, "verified": True}

    def get_source_volume(self, *, source_name: str) -> dict[str, object]:
        return self._source_status("GetSourceVolume", source_name)

    def set_source_volume(self, *, source_name: str, volume: float) -> dict[str, object]:
        if type(volume) not in (int, float) or not math.isfinite(volume) or not 0 <= volume <= 20:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        value = float(volume)
        return self._source_mutation(
            "SetSourceVolume", "GetSourceVolume", source_name, {"volume": value}, "volume", value
        )

    def get_source_mute(self, *, source_name: str) -> dict[str, object]:
        return self._source_status("GetSourceMute", source_name)

    def set_source_mute(self, *, source_name: str, muted: bool) -> dict[str, object]:
        if type(muted) is not bool:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._source_mutation(
            "SetSourceMute", "GetSourceMute", source_name, {"muted": muted}, "muted", muted
        )

    def get_source_monitor_type(self, *, source_name: str) -> dict[str, object]:
        return self._source_status("GetSourceMonitorType", source_name)

    def set_source_monitor_type(self, *, source_name: str, monitor_type: str) -> dict[str, object]:
        if monitor_type not in SOURCE_MONITOR_TYPES:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._source_mutation(
            "SetSourceMonitorType",
            "GetSourceMonitorType",
            source_name,
            {"monitorType": monitor_type},
            "monitorType",
            monitor_type,
        )

    def get_media_status(self, *, source_name: str) -> dict[str, object]:
        return self._source_status("GetMediaStatus", source_name)

    def play_media(self, *, source_name: str) -> dict[str, object]:
        return self._media_mutation("PlayMedia", source_name, "playing")

    def pause_media(self, *, source_name: str) -> dict[str, object]:
        return self._media_mutation("PauseMedia", source_name, "paused")

    def restart_media(self, *, source_name: str) -> dict[str, object]:
        return self._media_mutation("RestartMedia", source_name, "playing")

    def stop_media(self, *, source_name: str) -> dict[str, object]:
        return self._media_mutation("StopMedia", source_name, "stopped")

    def seek_media(self, *, source_name: str, media_cursor_ms: int) -> dict[str, object]:
        if type(media_cursor_ms) is not int or not 0 <= media_cursor_ms <= 86_400_000:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return self._media_mutation(
            "SeekMedia", source_name, None, {"mediaCursorMs": media_cursor_ms}, media_cursor_ms
        )

    def _source_status(self, request_type: str, source_name: str) -> dict[str, object]:
        return self._checked(
            request_type,
            {"sourceName": self._require_name(source_name)},
            deadline=self._operation_deadline(),
        )

    def _source_mutation(
        self,
        request_type: str,
        readback_type: str,
        source_name: str,
        values: Mapping[str, object],
        field: str,
        expected: object,
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)

        def matches(result: Mapping[str, object]) -> bool:
            actual = result.get(field)
            if (
                field == "volume"
                and type(actual) in (int, float)
                and type(expected) in (int, float)
            ):
                return math.isclose(
                    float(actual),
                    float(expected),
                    rel_tol=0.0,
                    abs_tol=SOURCE_VOLUME_READBACK_TOLERANCE,
                )
            return actual == expected

        return self._mutate_and_reconcile(
            request_type,
            {"sourceName": source_name, **values, "capability": "audio"},
            readback_type,
            {"sourceName": source_name},
            matches,
        )

    def _media_mutation(
        self,
        request_type: str,
        source_name: str,
        expected_state: str | None,
        values: Mapping[str, object] | None = None,
        expected_cursor: int | None = None,
    ) -> dict[str, object]:
        source_name = self._require_name(source_name)
        return self._mutate_and_reconcile(
            request_type,
            {"sourceName": source_name, **(values or {}), "capability": "media"},
            "GetMediaStatus",
            {"sourceName": source_name},
            lambda result: (
                (expected_state is None or result.get("mediaState") == expected_state)
                and (
                    expected_cursor is None
                    or (
                        type(result.get("mediaCursorMs")) is int
                        and abs(result["mediaCursorMs"] - expected_cursor)
                        <= MEDIA_SEEK_READBACK_TOLERANCE_MS
                    )
                )
            ),
        )

    def _mutate_and_reconcile(
        self,
        request_type: str,
        payload: Mapping[str, object],
        readback_type: str,
        readback_payload: Mapping[str, object],
        matches: Callable[[Mapping[str, object]], bool],
    ) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(readback_type, readback_payload, deadline=deadline)
            if matches(readback):
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                self._poll(deadline)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    @staticmethod
    def _require_schema_version(schema_version: object) -> str:
        if schema_version != TYPED_SETTINGS_SCHEMA_VERSION:
            raise BridgeError("OBS_SCHEMA_UNSUPPORTED")
        return TYPED_SETTINGS_SCHEMA_VERSION

    @classmethod
    def _normalize_input_settings(
        cls, source_kind: object, schema_version: object, settings: object
    ) -> dict[str, int]:
        cls._require_schema_version(schema_version)
        if source_kind not in REVIEWED_INPUT_KINDS:
            raise BridgeError("OBS_SOURCE_KIND_UNSUPPORTED")
        if (
            not isinstance(settings, Mapping)
            or not settings
            or not set(settings)
            <= {
                "width",
                "height",
                "color",
            }
        ):
            raise BridgeError("OBS_ARGUMENT_INVALID")
        normalized: dict[str, int] = {}
        for key, value in settings.items():
            maximum = 8192 if key in {"width", "height"} else 2**32 - 1
            minimum = 1 if key in {"width", "height"} else 0
            if type(value) is not int or not minimum <= value <= maximum:
                raise BridgeError("OBS_ARGUMENT_INVALID")
            normalized[key] = value
        return normalized

    @classmethod
    def _normalize_filter_settings(
        cls, filter_kind: object, schema_version: object, settings: object
    ) -> dict[str, float]:
        cls._require_schema_version(schema_version)
        if filter_kind not in REVIEWED_FILTER_KINDS:
            raise BridgeError("OBS_FILTER_KIND_UNSUPPORTED")
        if not isinstance(settings, Mapping) or set(settings) != {"db"}:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        value = settings["db"]
        if type(value) not in (int, float) or not math.isfinite(value) or not -30 <= value <= 30:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        return {"db": float(value)}

    def _domain_mutation(
        self, request_type: str, status_request: str, field: str, expected: bool
    ) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(status_request, deadline=deadline)
            if readback.get(field) is expected:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _output_mutation(
        self, request_type: str, output_name: str, expected: bool
    ) -> dict[str, object]:
        if not isinstance(output_name, str) or not output_name or len(output_name) > 256:
            raise BridgeError("OBS_ARGUMENT_INVALID")
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, {"outputName": output_name}, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked(
                "GetOutputStatus", {"outputName": output_name}, deadline=deadline
            )
            if (
                readback.get("outputName") == output_name
                and readback.get("outputKind") == output_name
                and readback.get("outputActive") is expected
            ):
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _recording_mutation(
        self, request_type: str, *, active: bool, paused: bool
    ) -> dict[str, object]:
        deadline = self._operation_deadline()
        accepted = self._checked(request_type, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self._checked("GetRecordingStatus", deadline=deadline)
            if readback.get("outputActive") is active and readback.get("outputPaused") is paused:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise BridgeError("OBS_TIMEOUT")
                self._sleeper(min(self._postcondition_poll_seconds, remaining))
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _request(
        self,
        request_type: str,
        data: Mapping[str, object] | None = None,
        *,
        deadline: float,
    ) -> dict[str, object]:
        if self._clock() >= deadline:
            raise BridgeError("OBS_TIMEOUT")
        try:
            response = self._transport.vendor_request(request_type, data or {}, deadline=deadline)
        except Exception as exc:
            try:
                raw_code = getattr(exc, "code", None)
            except Exception:
                raw_code = None
            raise BridgeError(
                _public_error_code(raw_code, fallback="OBS_CONNECTION_FAILED")
            ) from None
        if self._clock() > deadline:
            raise BridgeError("OBS_TIMEOUT")
        if not isinstance(response, dict):
            raise BridgeError("OBS_RESPONSE_INVALID")
        if response.get("ok") is False:
            raise BridgeError(
                _public_error_code(response.get("errorCode"), fallback="OBS_REQUEST_FAILED")
            )
        return response

    def _checked(
        self,
        request_type: str,
        data: Mapping[str, object] | None = None,
        *,
        deadline: float,
    ) -> dict[str, object]:
        with self._request_lock:
            response = self._request(request_type, data, deadline=deadline)
            if self._parse_identity(response) != self._identity:
                raise BridgeError("OBS_INSTANCE_DRIFT")
            event_sequence = self._parse_event_sequence(response)
            if event_sequence <= self._event_sequence:
                raise BridgeError("OBS_EVENT_SEQUENCE_INVALID")
            self._validate_read_only_response(request_type, response)
            self._event_sequence = event_sequence
            return response

    @staticmethod
    def _validate_read_only_response(request_type: str, response: Mapping[str, object]) -> None:
        if request_type in {
            "GetPluginStatus",
            "ListWindowCaptureCandidates",
            "ListScenes",
            "GetCurrentScene",
            "ListSceneItems",
            "GetSceneItem",
            "ListTransitions",
            "GetCurrentTransition",
            "GetStudioModeStatus",
            "GetCurrentPreviewScene",
            "ListProfiles",
            "GetCurrentProfile",
            "ListSceneCollections",
            "GetCurrentSceneCollection",
            "ListSources",
            "GetSourceIdentity",
            "ListInputKinds",
            "GetInputSettings",
            "DescribeProperties",
            "ValidatePropertyValue",
            "ListFilters",
            "GetFilter",
            "GetSourceVolume",
            "GetSourceMute",
            "GetSourceMonitorType",
            "GetMediaStatus",
            "GetRecordingStatus",
            "GetStreamingStatus",
            "GetReplayBufferStatus",
            "GetVirtualCameraStatus",
            "ListOutputs",
            "GetOutputStatus",
            "ListAllowlistedHotkeys",
            "GetOperatorStatus",
            "GetAgentInputOverlay",
            "GetSceneRecordingSession",
            "CaptureScreenshot",
            "CaptureProgramFrame",
        } and (not set(response) >= _IDENTITY_KEYS or response.get("ok") is not True):
            raise BridgeError("OBS_RESPONSE_INVALID")
        if request_type == "GetPluginStatus":
            if set(response) - (_IDENTITY_KEYS | {"ready"}) or response.get("ready") is not True:
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListScenes":
            allowed = _IDENTITY_KEYS | {"currentSceneName", "scenes", "truncated"}
            scenes = response.get("scenes")
            current = response.get("currentSceneName")
            if (
                set(response) - allowed
                or not isinstance(scenes, list)
                or len(scenes) > 256
                or type(response.get("truncated")) is not bool
                or (current is not None and (not isinstance(current, str) or len(current) > 256))
                or any(
                    not isinstance(scene, dict)
                    or set(scene) != {"sceneName"}
                    or not isinstance(scene.get("sceneName"), str)
                    or not scene["sceneName"]
                    or len(scene["sceneName"]) > 256
                    for scene in scenes
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListWindowCaptureCandidates":
            allowed = _IDENTITY_KEYS | {
                "executable",
                "windowTitle",
                "candidates",
                "truncated",
            }
            candidates = response.get("candidates")
            window_title = response.get("windowTitle")
            if (
                set(response) - allowed
                or not isinstance(response.get("executable"), str)
                or not 1 <= len(response["executable"]) <= 256
                or (
                    window_title is not None
                    and (not isinstance(window_title, str) or not 1 <= len(window_title) <= 256)
                )
                or not isinstance(candidates, list)
                or len(candidates) > 64
                or type(response.get("truncated")) is not bool
                or any(
                    not ObsControlBridge._valid_window_capture_candidate(candidate)
                    for candidate in candidates
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetCurrentScene":
            allowed = _IDENTITY_KEYS | {"sceneName"}
            if (
                set(response) - allowed
                or not isinstance(response.get("sceneName"), str)
                or not 1 <= len(response["sceneName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type in {"ListSceneItems", "GetSceneItem"}:
            allowed = _IDENTITY_KEYS | {
                "sceneName",
                "sceneItemId",
                "sourceName",
                "sourceKind",
                "enabled",
                "sceneItems",
                "truncated",
                "exists",
                "removed",
                "posX",
                "posY",
                "scaleX",
                "scaleY",
                "rotation",
            }
            if set(response) - allowed or not isinstance(response.get("sceneName"), str):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if "sceneItemId" in response and (
                type(response["sceneItemId"]) is not int or response["sceneItemId"] <= 0
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "ListSceneItems":
                items = response.get("sceneItems")
                if (
                    not isinstance(items, list)
                    or len(items) > 512
                    or type(response.get("truncated")) is not bool
                    or any(not ObsControlBridge._valid_scene_item(item) for item in items)
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            elif response.get("exists") is False or response.get("removed") is True:
                if set(response) - (
                    _IDENTITY_KEYS | {"sceneName", "sceneItemId", "exists", "removed"}
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            else:
                item_payload = {
                    key: response[key]
                    for key in ("sceneName", "sceneItemId", "sourceName", "sourceKind", "enabled")
                    if key in response
                }
                if not ObsControlBridge._valid_scene_item(item_payload):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetWindowCaptureSource":
            if not ObsControlBridge._valid_window_capture_source(response):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetAgentInputOverlay":
            allowed = _IDENTITY_KEYS | {
                "sceneName",
                "sceneItemId",
                "sourceName",
                "sourceKind",
                "theme",
                "anchor",
                "opacity",
                "margin",
                "agentId",
                "active",
                "activitySequence",
                "eventKind",
                "keysCsv",
                "mouseButton",
                "wheelDirection",
                "characterCount",
                "cueLabel",
                "durationMs",
                "remainingMs",
            }
            keys_csv = response.get("keysCsv")
            keys = keys_csv.split(",") if isinstance(keys_csv, str) and keys_csv else []
            if (
                set(response) != allowed
                or not isinstance(response.get("sceneName"), str)
                or not 1 <= len(response["sceneName"]) <= 256
                or type(response.get("sceneItemId")) is not int
                or response["sceneItemId"] <= 0
                or not isinstance(response.get("sourceName"), str)
                or not 1 <= len(response["sourceName"]) <= 256
                or response.get("sourceKind") != AGENT_INPUT_OVERLAY_SOURCE_KIND
                or response.get("theme") != AGENT_INPUT_OVERLAY_THEME
                or response.get("anchor") not in AGENT_INPUT_OVERLAY_ANCHORS | {"custom"}
                or type(response.get("opacity")) is not int
                or not AGENT_INPUT_OVERLAY_MIN_OPACITY
                <= response["opacity"]
                <= AGENT_INPUT_OVERLAY_MAX_OPACITY
                or type(response.get("margin")) is not int
                or not AGENT_INPUT_OVERLAY_MIN_MARGIN
                <= response["margin"]
                <= AGENT_INPUT_OVERLAY_MAX_MARGIN
                or not isinstance(response.get("agentId"), str)
                or len(response["agentId"]) > 64
                or type(response.get("active")) is not bool
                or type(response.get("activitySequence")) is not int
                or response["activitySequence"] < 0
                or response.get("eventKind") not in AGENT_INPUT_EVENT_KINDS | {"none"}
                or not isinstance(keys_csv, str)
                or len(keys_csv) > 32
                or len(keys) > 4
                or any(type(key) is not str or key not in AGENT_INPUT_KEYS for key in keys)
                or response.get("mouseButton") not in AGENT_INPUT_MOUSE_BUTTONS
                or response.get("wheelDirection") not in AGENT_INPUT_WHEEL_DIRECTIONS
                or type(response.get("characterCount")) is not int
                or not 0 <= response["characterCount"] <= 10000
                or not isinstance(response.get("cueLabel"), str)
                or len(response["cueLabel"]) > 96
                or type(response.get("durationMs")) is not int
                or not 0 <= response["durationMs"] <= 5000
                or type(response.get("remainingMs")) is not int
                or not 0 <= response["remainingMs"] <= 5000
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetSceneRecordingSession":
            allowed = _IDENTITY_KEYS | {"sessionId", "sessionActive", "startedAt", "recordings"}
            recordings = response.get("recordings")
            if (
                set(response) != allowed
                or not isinstance(response.get("sessionId"), str)
                or not 1 <= len(response["sessionId"]) <= 128
                or type(response.get("sessionActive")) is not bool
                or not isinstance(response.get("startedAt"), str)
                or not 1 <= len(response["startedAt"]) <= 64
                or not isinstance(recordings, list)
                or not 1 <= len(recordings) <= 8
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            seen_scenes: set[str] = set()
            recording_fields = {
                "sceneName",
                "fileName",
                "outputPath",
                "outputActive",
                "videoOnly",
                "videoWidth",
                "videoHeight",
                "totalBytes",
                "totalFrames",
                "lastError",
            }
            for item in recordings:
                if not isinstance(item, Mapping) or set(item) != recording_fields:
                    raise BridgeError("OBS_RESPONSE_INVALID")
                scene_name = item.get("sceneName")
                file_name = item.get("fileName")
                output_path = item.get("outputPath")
                path_file_name = str(output_path).replace("\\", "/").rsplit("/", 1)[-1]
                if (
                    not isinstance(scene_name, str)
                    or not 1 <= len(scene_name) <= 256
                    or scene_name in seen_scenes
                    or not isinstance(file_name, str)
                    or not file_name.lower().endswith(".mp4")
                    or len(file_name) > 160
                    or not isinstance(output_path, str)
                    or not 1 <= len(output_path) <= 4096
                    or path_file_name != file_name
                    or type(item.get("outputActive")) is not bool
                    or item.get("videoOnly") is not True
                    or type(item.get("videoWidth")) is not int
                    or not 1 <= item["videoWidth"] <= 16384
                    or type(item.get("videoHeight")) is not int
                    or not 1 <= item["videoHeight"] <= 16384
                    or type(item.get("totalBytes")) is not int
                    or item["totalBytes"] < 0
                    or type(item.get("totalFrames")) is not int
                    or item["totalFrames"] < 0
                    or not isinstance(item.get("lastError"), str)
                    or len(item["lastError"]) > 128
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
                seen_scenes.add(scene_name)
            return
        if request_type in {"ListTransitions", "GetCurrentTransition"}:
            allowed = _IDENTITY_KEYS | {
                "transitions",
                "truncated",
                "transitionName",
                "currentTransitionName",
                "durationMs",
            }
            if set(response) - allowed:
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "ListTransitions":
                transitions = response.get("transitions")
                if (
                    not isinstance(transitions, list)
                    or len(transitions) > 128
                    or type(response.get("truncated")) is not bool
                    or any(
                        not isinstance(item, dict)
                        or set(item) - {"transitionName", "transitionKind"}
                        or "transitionName" not in item
                        or not isinstance(item.get("transitionName"), str)
                        or not 1 <= len(item["transitionName"]) <= 256
                        or (
                            "transitionKind" in item
                            and (
                                not isinstance(item["transitionKind"], str)
                                or not 1 <= len(item["transitionKind"]) <= 256
                            )
                        )
                        for item in transitions
                    )
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            elif (
                not isinstance(response.get("transitionName"), str)
                or not 1 <= len(response["transitionName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if "currentTransitionName" in response and (
                not isinstance(response["currentTransitionName"], str)
                or not 1 <= len(response["currentTransitionName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if "durationMs" in response and (
                type(response["durationMs"]) is not int
                or not 0 <= response["durationMs"] <= 3_600_000
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetCurrentPreviewScene":
            if (
                set(response) - (_IDENTITY_KEYS | {"sceneName"})
                or not isinstance(response.get("sceneName"), str)
                or not 1 <= len(response["sceneName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetStudioModeStatus":
            allowed = _IDENTITY_KEYS | {"studioModeEnabled", "previewSceneName", "programSceneName"}
            if (
                set(response) - allowed
                or type(response.get("studioModeEnabled")) is not bool
                or any(
                    key in response
                    and (not isinstance(response[key], str) or not 1 <= len(response[key]) <= 256)
                    for key in ("previewSceneName", "programSceneName")
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type in {"ListProfiles", "ListSceneCollections"}:
            collection_key, name_key = (
                ("profiles", "profileName")
                if request_type == "ListProfiles"
                else ("sceneCollections", "sceneCollectionName")
            )
            current_key = (
                "currentProfileName"
                if request_type == "ListProfiles"
                else "currentSceneCollectionName"
            )
            allowed = _IDENTITY_KEYS | {
                collection_key,
                "truncated",
                current_key,
            }
            entries = response.get(collection_key)
            if (
                set(response) - allowed
                or not isinstance(entries, list)
                or len(entries) > 128
                or type(response.get("truncated")) is not bool
                or (
                    current_key in response
                    and (
                        not isinstance(response[current_key], str)
                        or not 1 <= len(response[current_key]) <= 256
                    )
                )
                or any(
                    not isinstance(item, dict)
                    or set(item) != {name_key}
                    or not isinstance(item.get(name_key), str)
                    or not 1 <= len(item[name_key]) <= 256
                    for item in entries
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type in {"GetCurrentProfile", "GetCurrentSceneCollection"}:
            field = "profileName" if request_type == "GetCurrentProfile" else "sceneCollectionName"
            if (
                set(response) - (_IDENTITY_KEYS | {field})
                or not isinstance(response.get(field), str)
                or not 1 <= len(response[field]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListSources":
            allowed = _IDENTITY_KEYS | {"sceneName", "sources", "truncated"}
            sources = response.get("sources")
            scene_name = response.get("sceneName")
            if (
                set(response) - allowed
                or not isinstance(scene_name, str)
                or not scene_name
                or len(scene_name) > 256
                or not isinstance(sources, list)
                or len(sources) > 512
                or type(response.get("truncated")) is not bool
                or any(not ObsControlBridge._valid_source(source) for source in sources)
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetSourceIdentity":
            allowed = _IDENTITY_KEYS | {
                "sourceName",
                "sourceKind",
                "sourceType",
                "outputFlags",
                "active",
                "showing",
                "width",
                "height",
            }
            if (
                set(response) != allowed
                or any(
                    not isinstance(response.get(key), str) or not 1 <= len(response[key]) <= 256
                    for key in ("sourceName", "sourceKind")
                )
                or response.get("sourceType")
                not in {
                    "input",
                    "filter",
                    "scene",
                    "transition",
                    "unknown",
                }
                or type(response.get("outputFlags")) is not int
                or not 0 <= response["outputFlags"] <= 2**32 - 1
                or type(response.get("active")) is not bool
                or type(response.get("showing")) is not bool
                or type(response.get("width")) is not int
                or not 0 <= response["width"] <= 16384
                or type(response.get("height")) is not int
                or not 0 <= response["height"] <= 16384
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListInputKinds":
            kinds = response.get("inputKinds")
            if (
                set(response) != _IDENTITY_KEYS | {"schemaVersion", "inputKinds"}
                or response.get("schemaVersion") != TYPED_SETTINGS_SCHEMA_VERSION
                or not isinstance(kinds, list)
                or not 1 <= len(kinds) <= 16
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"sourceKind", "displayName"}
                    or item.get("sourceKind") not in REVIEWED_INPUT_KINDS
                    or not isinstance(item.get("displayName"), str)
                    or not 1 <= len(item["displayName"]) <= 128
                    for item in kinds
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetInputSettings":
            settings = response.get("settings")
            if (
                set(response)
                != _IDENTITY_KEYS | {"sourceName", "sourceKind", "schemaVersion", "settings"}
                or not isinstance(response.get("sourceName"), str)
                or not 1 <= len(response["sourceName"]) <= 256
                or response.get("sourceKind") not in REVIEWED_INPUT_KINDS
                or response.get("schemaVersion") != TYPED_SETTINGS_SCHEMA_VERSION
                or not isinstance(settings, Mapping)
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            ObsControlBridge._normalize_input_settings(
                response["sourceKind"], response["schemaVersion"], settings
            )
            return
        if request_type == "DescribeProperties":
            properties = response.get("properties")
            if (
                set(response) != _IDENTITY_KEYS | {"sourceKind", "schemaVersion", "properties"}
                or response.get("sourceKind") not in REVIEWED_INPUT_KINDS
                or response.get("schemaVersion") != TYPED_SETTINGS_SCHEMA_VERSION
                or not isinstance(properties, list)
                or {item.get("propertyName") for item in properties} != {"width", "height", "color"}
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"propertyName", "valueType", "minimum", "maximum"}
                    or item.get("valueType") != "integer"
                    or type(item.get("minimum")) is not int
                    or type(item.get("maximum")) is not int
                    or item["minimum"] > item["maximum"]
                    for item in properties
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ValidatePropertyValue":
            if (
                set(response)
                != _IDENTITY_KEYS
                | {"sourceKind", "schemaVersion", "propertyName", "value", "valid"}
                or response.get("sourceKind") not in REVIEWED_INPUT_KINDS
                or response.get("schemaVersion") != TYPED_SETTINGS_SCHEMA_VERSION
                or response.get("propertyName") not in {"width", "height", "color"}
                or type(response.get("value")) is not int
                or response.get("valid") is not True
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            ObsControlBridge._normalize_input_settings(
                response["sourceKind"],
                response["schemaVersion"],
                {response["propertyName"]: response["value"]},
            )
            return
        if request_type == "ListFilters":
            filters = response.get("filters")
            if (
                set(response) != _IDENTITY_KEYS | {"sourceName", "filters", "truncated"}
                or not isinstance(response.get("sourceName"), str)
                or not 1 <= len(response["sourceName"]) <= 256
                or not isinstance(filters, list)
                or len(filters) > 64
                or type(response.get("truncated")) is not bool
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"filterName", "filterKind", "enabled"}
                    or not isinstance(item.get("filterName"), str)
                    or not 1 <= len(item["filterName"]) <= 256
                    or not isinstance(item.get("filterKind"), str)
                    or not 1 <= len(item["filterKind"]) <= 256
                    or type(item.get("enabled")) is not bool
                    for item in filters
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetFilter":
            settings = response.get("settings")
            if (
                set(response)
                != _IDENTITY_KEYS
                | {
                    "sourceName",
                    "filterName",
                    "filterKind",
                    "enabled",
                    "schemaVersion",
                    "settings",
                }
                or any(
                    not isinstance(response.get(key), str) or not 1 <= len(response[key]) <= 256
                    for key in ("sourceName", "filterName")
                )
                or response.get("filterKind") not in REVIEWED_FILTER_KINDS
                or response.get("schemaVersion") != TYPED_SETTINGS_SCHEMA_VERSION
                or type(response.get("enabled")) is not bool
                or not isinstance(settings, Mapping)
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            ObsControlBridge._normalize_filter_settings(
                response["filterKind"], response["schemaVersion"], settings
            )
            return
        if request_type == "GetSourceVolume":
            if (
                set(response) != _IDENTITY_KEYS | {"sourceName", "volume"}
                or not isinstance(response.get("sourceName"), str)
                or type(response.get("volume")) not in (int, float)
                or not math.isfinite(response["volume"])
                or not 0 <= response["volume"] <= 20
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetSourceMute":
            if (
                set(response) != _IDENTITY_KEYS | {"sourceName", "muted"}
                or not isinstance(response.get("sourceName"), str)
                or type(response.get("muted")) is not bool
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetSourceMonitorType":
            if (
                set(response) != _IDENTITY_KEYS | {"sourceName", "monitorType"}
                or not isinstance(response.get("sourceName"), str)
                or response.get("monitorType") not in SOURCE_MONITOR_TYPES
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetMediaStatus":
            if (
                set(response)
                != _IDENTITY_KEYS | {"sourceName", "mediaState", "mediaDurationMs", "mediaCursorMs"}
                or not isinstance(response.get("sourceName"), str)
                or response.get("mediaState") not in MEDIA_STATES
                or type(response.get("mediaDurationMs")) is not int
                or not 0 <= response["mediaDurationMs"] <= 2**63 - 1
                or type(response.get("mediaCursorMs")) is not int
                or not 0 <= response["mediaCursorMs"] <= 2**63 - 1
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetRecordingStatus":
            diagnostic_strings = {
                "outputName": 256,
                "outputKind": 256,
                "outputPath": 4096,
                "lastError": 4096,
            }
            diagnostic_integers = {"totalBytes", "totalFrames"}
            allowed = (
                _IDENTITY_KEYS
                | {"outputActive", "outputPaused"}
                | set(diagnostic_strings)
                | diagnostic_integers
            )
            if (
                set(response) - allowed
                or type(response.get("outputActive")) is not bool
                or type(response.get("outputPaused")) is not bool
                or any(
                    key in response
                    and (not isinstance(response[key], str) or len(response[key]) > max_length)
                    for key, max_length in diagnostic_strings.items()
                )
                or any(
                    key in response
                    and (type(response[key]) is not int or not 0 <= response[key] <= 2**63 - 1)
                    for key in diagnostic_integers
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        bool_fields = {
            "GetStreamingStatus": "streamingActive",
            "GetReplayBufferStatus": "replayBufferActive",
            "GetVirtualCameraStatus": "virtualCameraActive",
        }
        if request_type in bool_fields:
            field = bool_fields[request_type]
            allowed = _IDENTITY_KEYS | {field}
            if set(response) - allowed or type(response.get(field)) is not bool:
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListOutputs":
            outputs = response.get("outputs")
            if (
                set(response) - (_IDENTITY_KEYS | {"outputs", "truncated"})
                or not isinstance(outputs, list)
                or len(outputs) > 64
                or type(response.get("truncated")) is not bool
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"outputName", "outputKind", "outputActive"}
                    or not isinstance(item.get("outputName"), str)
                    or not item["outputName"]
                    or len(item["outputName"]) > 256
                    or not isinstance(item.get("outputKind"), str)
                    or not item["outputKind"]
                    or len(item["outputKind"]) > 256
                    or type(item.get("outputActive")) is not bool
                    for item in outputs
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetOutputStatus":
            allowed = _IDENTITY_KEYS | {"outputName", "outputKind", "outputActive"}
            if (
                set(response) - allowed
                or not isinstance(response.get("outputName"), str)
                or not response["outputName"]
                or len(response["outputName"]) > 256
                or not isinstance(response.get("outputKind"), str)
                or not response["outputKind"]
                or len(response["outputKind"]) > 256
                or type(response.get("outputActive")) is not bool
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "ListAllowlistedHotkeys":
            hotkeys = response.get("hotkeys")
            if (
                set(response) - (_IDENTITY_KEYS | {"hotkeys", "truncated"})
                or not isinstance(hotkeys, list)
                or len(hotkeys) > 128
                or type(response.get("truncated")) is not bool
                or any(
                    not isinstance(item, dict)
                    or set(item) - {"hotkeyName", "description"}
                    or set(item) < {"hotkeyName"}
                    or not isinstance(item.get("hotkeyName"), str)
                    or not 1 <= len(item["hotkeyName"]) <= 128
                    or (
                        "description" in item
                        and (
                            not isinstance(item["description"], str)
                            or len(item["description"]) > 256
                        )
                    )
                    for item in hotkeys
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "GetOperatorStatus":
            allowed = _IDENTITY_KEYS | {
                "ready",
                "uiThreadReady",
                "configPathRedacted",
                "profileName",
                "sceneCollectionName",
                "allowlistedHotkeys",
                "configVersion",
            }
            if (
                set(response) - allowed
                or response.get("ready") is not True
                or response.get("uiThreadReady") is not True
                or response.get("configPathRedacted") is not True
                or (
                    "profileName" in response
                    and (
                        not isinstance(response["profileName"], str)
                        or not 1 <= len(response["profileName"]) <= 256
                    )
                )
                or (
                    "sceneCollectionName" in response
                    and (
                        not isinstance(response["sceneCollectionName"], str)
                        or not 1 <= len(response["sceneCollectionName"]) <= 256
                    )
                )
                or (
                    "allowlistedHotkeys" in response
                    and (
                        not isinstance(response["allowlistedHotkeys"], list)
                        or any(
                            not isinstance(item, str) or not 1 <= len(item) <= 128
                            for item in response["allowlistedHotkeys"]
                        )
                    )
                )
                or (
                    "configVersion" in response
                    and (
                        not isinstance(response["configVersion"], str)
                        or not 1 <= len(response["configVersion"]) <= 128
                    )
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "CaptureScreenshot":
            allowed = _IDENTITY_KEYS | {
                "accepted",
                "screenshotId",
                "imageFormat",
                "width",
                "height",
                "path",
                "pathRedacted",
            }
            if (
                set(response) - allowed
                or response.get("accepted") is not True
                or not isinstance(response.get("screenshotId"), str)
                or not 1 <= len(response["screenshotId"]) <= 256
                or response.get("imageFormat") not in {"png", "jpg", "jpeg", "webp"}
                or response.get("pathRedacted") is not True
                or ("path" in response and not isinstance(response["path"], str))
                or any(
                    key in response
                    and (
                        not isinstance(response[key], int)
                        or isinstance(response[key], bool)
                        or response[key] <= 0
                    )
                    for key in ("width", "height")
                )
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return
        if request_type == "CaptureProgramFrame":
            ObsControlBridge._decode_program_frame(response)
            return
        if request_type in {
            "StartRecording",
            "StopRecording",
            "PauseRecording",
            "ResumeRecording",
            "StartStreaming",
            "StopStreaming",
            "StartReplayBuffer",
            "StopReplayBuffer",
            "SaveReplayBuffer",
            "StartVirtualCamera",
            "StopVirtualCamera",
            "StartOutput",
            "StopOutput",
            "SetCurrentProfile",
            "SetCurrentSceneCollection",
            "SetCurrentScene",
            "CreateScene",
            "RenameScene",
            "RemoveScene",
            "CreateSceneItem",
            "CreateWindowCaptureSource",
            "RestoreWindowCaptureCandidate",
            "RebindWindowCaptureSource",
            "SetWindowCaptureMethod",
            "SetSceneItemEnabled",
            "SetSceneItemTransform",
            "RemoveSceneItem",
            "SetCurrentTransition",
            "TriggerTransition",
            "SetStudioMode",
            "SetCurrentPreviewScene",
            "TriggerStudioModeTransition",
            "TriggerAllowlistedHotkey",
            "RequestGracefulShutdown",
            "CreateAgentInputOverlay",
            "SetAgentInputOverlayLayout",
            "EmitAgentInputActivity",
            "ClearAgentInputOverlay",
            "StartSceneRecordings",
            "StopSceneRecordings",
            "CreateSource",
            "RenameSource",
            "RemoveSource",
            "SetInputSettings",
            "SetPropertyValue",
            "CreateFilter",
            "SetFilterEnabled",
            "SetFilterSettings",
            "RemoveFilter",
            "SetSourceVolume",
            "SetSourceMute",
            "SetSourceMonitorType",
            "PlayMedia",
            "PauseMedia",
            "RestartMedia",
            "StopMedia",
            "SeekMedia",
        }:
            allowed = set(_IDENTITY_KEYS) | {"accepted"}
            if request_type == "SetCurrentProfile":
                allowed.add("profileName")
            elif request_type == "SetCurrentSceneCollection":
                allowed.add("sceneCollectionName")
            elif request_type == "TriggerAllowlistedHotkey":
                allowed.add("hotkeyName")
            elif request_type in {"SetCurrentScene", "SetCurrentPreviewScene"}:
                allowed.add("sceneName")
            elif request_type in {"CreateScene", "RemoveScene"}:
                allowed |= {"sceneName", "capability"}
            elif request_type == "RenameScene":
                allowed |= {"sceneName", "newSceneName", "capability"}
            elif request_type == "SetCurrentTransition":
                allowed |= {"transitionName", "durationMs"}
            elif request_type in {
                "CreateSceneItem",
                "CreateWindowCaptureSource",
                "RebindWindowCaptureSource",
                "SetWindowCaptureMethod",
                "SetSceneItemEnabled",
                "SetSceneItemTransform",
                "RemoveSceneItem",
            }:
                allowed |= {
                    "sceneName",
                    "sceneItemId",
                    "sourceName",
                    "sourceKind",
                    "enabled",
                    "capability",
                    "posX",
                    "posY",
                    "scaleX",
                    "scaleY",
                    "rotation",
                }
                if request_type in {
                    "CreateWindowCaptureSource",
                    "RebindWindowCaptureSource",
                    "SetWindowCaptureMethod",
                }:
                    allowed |= {
                        "processId",
                        "windowHandle",
                        "windowTitle",
                        "windowClass",
                        "executable",
                        "captureCursor",
                        "clientArea",
                        "captureMethod",
                        "bindingVerified",
                    }
            elif request_type == "RestoreWindowCaptureCandidate":
                allowed |= {
                    "processId",
                    "windowHandle",
                    "windowTitle",
                    "windowClass",
                    "executable",
                    "visible",
                    "minimized",
                    "clientWidth",
                    "clientHeight",
                    "captureReady",
                    "capability",
                }
            elif request_type in {"TriggerStudioModeTransition", "TriggerTransition"}:
                allowed.add("capability")
                if request_type == "TriggerTransition":
                    allowed.add("sceneName")
            elif request_type == "SetStudioMode":
                allowed |= {"studioModeEnabled", "capability"}
            if request_type == "SaveReplayBuffer":
                allowed |= {"submitted"}
            elif request_type == "RequestGracefulShutdown":
                allowed |= {"shutdownScheduled"}
            elif request_type in {"EmitAgentInputActivity", "ClearAgentInputOverlay"}:
                allowed |= {"activitySequence"}
            elif request_type in {"StartSceneRecordings", "StopSceneRecordings"}:
                allowed |= {"sessionId"}
            elif request_type in {"RemoveSource", "RemoveFilter"}:
                allowed |= {"removed"}
            elif request_type == "RenameSource":
                allowed |= {"newSourceName"}
            if (
                set(response) - allowed
                or response.get("ok") is not True
                or type(response.get("accepted")) is not bool
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type in {"SetCurrentScene", "SetCurrentPreviewScene"} and (
                not isinstance(response.get("sceneName"), str)
                or not 1 <= len(response["sceneName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "SetCurrentTransition" and (
                not isinstance(response.get("transitionName"), str)
                or not 1 <= len(response["transitionName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "TriggerTransition" and (
                not isinstance(response.get("sceneName"), str)
                or not 1 <= len(response["sceneName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type in {
                "CreateSceneItem",
                "CreateWindowCaptureSource",
                "RebindWindowCaptureSource",
                "SetWindowCaptureMethod",
                "SetSceneItemEnabled",
                "SetSceneItemTransform",
                "RemoveSceneItem",
            }:
                if request_type in {
                    "CreateWindowCaptureSource",
                    "RebindWindowCaptureSource",
                    "SetWindowCaptureMethod",
                }:
                    if not ObsControlBridge._valid_window_capture_source(response, mutation=True):
                        raise BridgeError("OBS_RESPONSE_INVALID")
                elif request_type != "RemoveSceneItem":
                    item_payload = {
                        key: response[key]
                        for key in (
                            "sceneName",
                            "sceneItemId",
                            "sourceName",
                            "sourceKind",
                            "enabled",
                        )
                        if key in response
                    }
                    if not ObsControlBridge._valid_scene_item(item_payload):
                        raise BridgeError("OBS_RESPONSE_INVALID")
                if request_type == "RemoveSceneItem" and (
                    type(response.get("sceneItemId")) is not int
                    or response["sceneItemId"] <= 0
                    or not isinstance(response.get("sceneName"), str)
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "RestoreWindowCaptureCandidate":
                candidate = {
                    key: response[key]
                    for key in (
                        "processId",
                        "windowHandle",
                        "windowTitle",
                        "windowClass",
                        "executable",
                        "visible",
                        "minimized",
                        "clientWidth",
                        "clientHeight",
                        "captureReady",
                    )
                    if key in response
                }
                if (
                    not ObsControlBridge._valid_window_capture_candidate(candidate)
                    or response.get("capability") != "window_capture"
                ):
                    raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "TriggerAllowlistedHotkey" and (
                not isinstance(response.get("hotkeyName"), str)
                or not 1 <= len(response["hotkeyName"]) <= 128
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if (
                request_type == "SaveReplayBuffer"
                and "submitted" in response
                and type(response.get("submitted")) is not bool
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if (
                request_type == "RequestGracefulShutdown"
                and response.get("shutdownScheduled") is not True
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type in {"EmitAgentInputActivity", "ClearAgentInputOverlay"} and (
                type(response.get("activitySequence")) is not int
                or response["activitySequence"] < 1
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type in {"StartSceneRecordings", "StopSceneRecordings"} and (
                not isinstance(response.get("sessionId"), str)
                or not 1 <= len(response["sessionId"]) <= 128
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if (
                request_type in {"RemoveSource", "RemoveFilter"}
                and response.get("removed") is not True
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if request_type == "RenameSource" and (
                not isinstance(response.get("newSourceName"), str)
                or not 1 <= len(response["newSourceName"]) <= 256
            ):
                raise BridgeError("OBS_RESPONSE_INVALID")
            return

    @staticmethod
    def _valid_source(source: object) -> bool:
        if not isinstance(source, dict) or set(source) != {
            "sceneItemId",
            "sourceName",
            "sourceKind",
            "enabled",
        }:
            return False
        scene_item_id = source.get("sceneItemId")
        return (
            isinstance(scene_item_id, int)
            and not isinstance(scene_item_id, bool)
            and isinstance(source.get("sourceName"), str)
            and bool(source["sourceName"])
            and len(source["sourceName"]) <= 256
            and isinstance(source.get("sourceKind"), str)
            and bool(source["sourceKind"])
            and len(source["sourceKind"]) <= 256
            and type(source.get("enabled")) is bool
        )

    @staticmethod
    def _valid_scene_item(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        required = {"sceneName", "sceneItemId", "sourceName", "sourceKind", "enabled"}
        optional = {"posX", "posY", "scaleX", "scaleY", "rotation"}
        if not required <= set(item) <= required | optional:
            return False
        return (
            isinstance(item.get("sceneName"), str)
            and 1 <= len(item["sceneName"]) <= 256
            and type(item.get("sceneItemId")) is int
            and item["sceneItemId"] > 0
            and isinstance(item.get("sourceName"), str)
            and 1 <= len(item["sourceName"]) <= 256
            and isinstance(item.get("sourceKind"), str)
            and 1 <= len(item["sourceKind"]) <= 256
            and type(item.get("enabled")) is bool
            and all(
                key not in item
                or (
                    type(item[key]) in (int, float)
                    and not isinstance(item[key], bool)
                    and math.isfinite(float(item[key]))
                )
                for key in optional
            )
        )

    @staticmethod
    def _valid_window_capture_candidate(candidate: object) -> bool:
        if not isinstance(candidate, dict) or set(candidate) != {
            "processId",
            "windowHandle",
            "windowTitle",
            "windowClass",
            "executable",
            "visible",
            "minimized",
            "clientWidth",
            "clientHeight",
            "captureReady",
        }:
            return False
        return (
            type(candidate.get("processId")) is int
            and 1 <= candidate["processId"] < 2**32
            and type(candidate.get("windowHandle")) is int
            and 1 <= candidate["windowHandle"] < 2**63
            and all(
                isinstance(candidate.get(key), str) and 1 <= len(candidate[key]) <= 256
                for key in ("windowTitle", "windowClass", "executable")
            )
            and type(candidate.get("visible")) is bool
            and type(candidate.get("minimized")) is bool
            and type(candidate.get("clientWidth")) is int
            and 0 <= candidate["clientWidth"] < 2**31
            and type(candidate.get("clientHeight")) is int
            and 0 <= candidate["clientHeight"] < 2**31
            and type(candidate.get("captureReady")) is bool
            and candidate["captureReady"]
            is (
                candidate["visible"]
                and not candidate["minimized"]
                and candidate["clientWidth"] > 0
                and candidate["clientHeight"] > 0
            )
        )

    @staticmethod
    def _valid_window_capture_source(
        source: Mapping[str, object], *, mutation: bool = False
    ) -> bool:
        allowed = _IDENTITY_KEYS | {
            "sceneName",
            "sceneItemId",
            "sourceName",
            "sourceKind",
            "enabled",
            "processId",
            "windowHandle",
            "windowTitle",
            "windowClass",
            "executable",
            "captureCursor",
            "clientArea",
            "captureMethod",
            "bindingVerified",
        }
        if mutation:
            allowed |= {"accepted", "capability"}
        required = allowed - ({"capability"} if mutation else set())
        if not required <= set(source) <= allowed:
            return False
        return (
            isinstance(source.get("sceneName"), str)
            and 1 <= len(source["sceneName"]) <= 256
            and type(source.get("sceneItemId")) is int
            and source["sceneItemId"] > 0
            and isinstance(source.get("sourceName"), str)
            and 1 <= len(source["sourceName"]) <= 256
            and source.get("sourceKind") == "window_capture"
            and type(source.get("enabled")) is bool
            and type(source.get("processId")) is int
            and 1 <= source["processId"] < 2**32
            and type(source.get("windowHandle")) is int
            and 1 <= source["windowHandle"] < 2**63
            and all(
                isinstance(source.get(key), str) and 1 <= len(source[key]) <= 256
                for key in ("windowTitle", "windowClass", "executable")
            )
            and type(source.get("captureCursor")) is bool
            and type(source.get("clientArea")) is bool
            and source.get("captureMethod") in WINDOW_CAPTURE_METHODS
            and source.get("bindingVerified") is True
            and (not mutation or source.get("accepted") is True)
            and ("capability" not in source or source.get("capability") == "window_capture")
        )

    def _operation_deadline(self) -> float:
        if self._bound_deadline is not None:
            return self._bound_deadline
        return self._clock() + DEFAULT_OPERATION_TIMEOUT_SECONDS

    @staticmethod
    def _decode_program_frame(response: Mapping[str, object]) -> bytes:
        allowed = _IDENTITY_KEYS | {"sourceName", "imageFormat", "imageData"}
        image_data = response.get("imageData")
        if (
            set(response) != allowed
            or not isinstance(response.get("sourceName"), str)
            or not 1 <= len(response["sourceName"]) <= 256
            or response.get("imageFormat") != "png"
            or not isinstance(image_data, str)
            or not image_data.startswith(_PNG_DATA_URL_PREFIX)
        ):
            raise BridgeError("OBS_RESPONSE_INVALID")
        try:
            image = base64.b64decode(image_data[len(_PNG_DATA_URL_PREFIX) :], validate=True)
        except (binascii.Error, ValueError):
            raise BridgeError("OBS_RESPONSE_INVALID") from None
        if (
            not 33 <= len(image) <= MAX_PROGRAM_FRAME_BYTES
            or not image.startswith(_PNG_SIGNATURE)
            or image[12:16] != b"IHDR"
            or int.from_bytes(image[16:20], "big") != PROGRAM_FRAME_WIDTH
            or int.from_bytes(image[20:24], "big") != PROGRAM_FRAME_HEIGHT
            or image[-8:-4] != b"IEND"
        ):
            raise BridgeError("OBS_RESPONSE_INVALID")
        offset = len(_PNG_SIGNATURE)
        chunk_count = 0
        saw_idat = False
        saw_iend = False
        while offset < len(image):
            chunk_count += 1
            if chunk_count > 128 or offset + 12 > len(image):
                raise BridgeError("OBS_RESPONSE_INVALID")
            chunk_length = int.from_bytes(image[offset : offset + 4], "big")
            chunk_type = image[offset + 4 : offset + 8]
            chunk_end = offset + 12 + chunk_length
            if chunk_end > len(image):
                raise BridgeError("OBS_RESPONSE_INVALID")
            chunk_payload = image[offset + 8 : offset + 8 + chunk_length]
            expected_crc = int.from_bytes(image[offset + 8 + chunk_length : chunk_end], "big")
            actual_crc = binascii.crc32(chunk_type + chunk_payload) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                raise BridgeError("OBS_RESPONSE_INVALID")
            if chunk_count == 1 and (chunk_type != b"IHDR" or chunk_length != 13):
                raise BridgeError("OBS_RESPONSE_INVALID")
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                if chunk_length != 0 or chunk_end != len(image):
                    raise BridgeError("OBS_RESPONSE_INVALID")
                saw_iend = True
            offset = chunk_end
        if not saw_idat or not saw_iend:
            raise BridgeError("OBS_RESPONSE_INVALID")
        return image

    @staticmethod
    def _parse_identity(response: Mapping[str, object]) -> _Identity:
        instance_id = response.get("instanceId")
        plugin_version = response.get("pluginVersion")
        obs_version = response.get("obsVersion")
        host_pid = response.get("hostPid")
        if (
            not isinstance(instance_id, str)
            or not instance_id
            or len(instance_id) > 128
            or not isinstance(plugin_version, str)
            or not plugin_version
            or not isinstance(obs_version, str)
            or not obs_version
            or not isinstance(host_pid, int)
            or isinstance(host_pid, bool)
            or host_pid <= 0
        ):
            raise BridgeError("OBS_RESPONSE_INVALID")
        return _Identity(instance_id, plugin_version, obs_version, host_pid)

    @staticmethod
    def _parse_event_sequence(response: Mapping[str, object]) -> int:
        event_sequence = response.get("eventSequence")
        if (
            not isinstance(event_sequence, int)
            or isinstance(event_sequence, bool)
            or event_sequence < 0
        ):
            raise BridgeError("OBS_RESPONSE_INVALID")
        return event_sequence


__all__ = ["ALLOWLISTED_HOTKEYS", "BridgeError", "ObsControlBridge", "VendorTransport"]
