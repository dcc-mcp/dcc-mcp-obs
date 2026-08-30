"""Bounded typed bridge to the native OBS plugin vendor contract."""

from __future__ import annotations

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
    }
)
_IDENTITY_KEYS = frozenset(
    {"instanceId", "pluginVersion", "obsVersion", "hostPid", "eventSequence", "ok"}
)


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
            enabled=enabled,
        )
        deadline = self._operation_deadline()
        accepted = self._checked("CreateWindowCaptureSource", payload, deadline=deadline)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        return self._readback_window_capture(payload, deadline=deadline)

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
            enabled=enabled,
        )
        return self._readback_window_capture(payload, deadline=self._operation_deadline())

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
        return {
            "sceneName": scene_name,
            "sourceName": source_name,
            "processId": process_id,
            "windowHandle": window_handle,
            "windowTitle": window_title,
            "captureCursor": capture_cursor,
            "clientArea": client_area,
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
            "GetRecordingStatus",
            "GetStreamingStatus",
            "GetReplayBufferStatus",
            "GetVirtualCameraStatus",
            "ListOutputs",
            "GetOutputStatus",
            "ListAllowlistedHotkeys",
            "GetOperatorStatus",
            "CaptureScreenshot",
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
        if request_type == "GetRecordingStatus":
            allowed = _IDENTITY_KEYS | {"outputActive", "outputPaused"}
            if (
                set(response) - allowed
                or type(response.get("outputActive")) is not bool
                or type(response.get("outputPaused")) is not bool
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
            "UpdateSceneItem",
            "SetSceneItemEnabled",
            "SetSceneItemTransform",
            "RemoveSceneItem",
            "SetCurrentTransition",
            "TriggerTransition",
            "SetStudioMode",
            "SetCurrentPreviewScene",
            "TriggerStudioModeTransition",
            "TriggerAllowlistedHotkey",
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
                "UpdateSceneItem",
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
                if request_type == "CreateWindowCaptureSource":
                    allowed |= {
                        "processId",
                        "windowHandle",
                        "windowTitle",
                        "windowClass",
                        "executable",
                        "captureCursor",
                        "clientArea",
                        "bindingVerified",
                    }
            elif request_type in {"TriggerStudioModeTransition", "TriggerTransition"}:
                allowed.add("capability")
                if request_type == "TriggerTransition":
                    allowed.add("sceneName")
            elif request_type == "SetStudioMode":
                allowed |= {"studioModeEnabled", "capability"}
            if request_type == "SaveReplayBuffer":
                allowed |= {"submitted"}
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
                "UpdateSceneItem",
                "SetSceneItemEnabled",
                "SetSceneItemTransform",
                "RemoveSceneItem",
            }:
                if request_type == "CreateWindowCaptureSource":
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
            and source.get("bindingVerified") is True
            and (not mutation or source.get("accepted") is True)
            and ("capability" not in source or source.get("capability") == "window_capture")
        )

    def _operation_deadline(self) -> float:
        if self._bound_deadline is not None:
            return self._bound_deadline
        return self._clock() + DEFAULT_OPERATION_TIMEOUT_SECONDS

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
