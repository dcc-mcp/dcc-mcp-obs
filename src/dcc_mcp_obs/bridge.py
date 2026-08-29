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
        "OBS_OUTPUT_NOT_FOUND",
        "OBS_OUTPUT_NOT_ACTIVE",
        "OBS_MUTATION_REJECTED",
        "OBS_TIMEOUT",
        "OBS_UI_TIMEOUT",
        "OBS_VERSION_UNSUPPORTED",
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
        postcondition_attempts: int = 6,
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
            "ListSources",
            "GetRecordingStatus",
            "GetStreamingStatus",
            "GetReplayBufferStatus",
            "GetVirtualCameraStatus",
            "ListOutputs",
            "GetOutputStatus",
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
        }:
            allowed = _IDENTITY_KEYS | {"accepted"}
            if request_type == "SaveReplayBuffer":
                allowed |= {"submitted"}
            if (
                set(response) - allowed
                or type(response.get("accepted")) is not bool
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


__all__ = ["BridgeError", "ObsControlBridge", "VendorTransport"]
