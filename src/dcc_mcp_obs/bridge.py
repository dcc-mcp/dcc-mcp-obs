"""Bounded typed bridge to the native OBS plugin vendor contract."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class VendorTransport(Protocol):
    def vendor_request(
        self, request_type: str, data: Mapping[str, object]
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
        try:
            status = self._request("GetPluginStatus")
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("OBS_CONNECTION_FAILED") from exc
        self._identity = self._parse_identity(status)
        if (
            self._identity.host_pid != expected_pid
            or (
                expected_instance_id is not None
                and self._identity.instance_id != expected_instance_id
            )
            or status.get("ready") is not True
        ):
            raise BridgeError("OBS_INSTANCE_NOT_READY")

    def status(self) -> dict[str, object]:
        return self._checked("GetPluginStatus")

    def list_scenes(self) -> dict[str, object]:
        return self._checked("ListScenes")

    def list_sources(self, *, scene_name: str | None = None) -> dict[str, object]:
        data: dict[str, object] = {}
        if scene_name is not None:
            if not isinstance(scene_name, str) or not scene_name or len(scene_name) > 256:
                raise BridgeError("OBS_ARGUMENT_INVALID")
            data["sceneName"] = scene_name
        return self._checked("ListSources", data)

    def recording_status(self) -> dict[str, object]:
        return self._checked("GetRecordingStatus")

    def start_recording(self) -> dict[str, object]:
        return self._recording_mutation("StartRecording", active=True, paused=False)

    def stop_recording(self) -> dict[str, object]:
        return self._recording_mutation("StopRecording", active=False, paused=False)

    def pause_recording(self) -> dict[str, object]:
        return self._recording_mutation("PauseRecording", active=True, paused=True)

    def resume_recording(self) -> dict[str, object]:
        return self._recording_mutation("ResumeRecording", active=True, paused=False)

    def _recording_mutation(
        self, request_type: str, *, active: bool, paused: bool
    ) -> dict[str, object]:
        accepted = self._checked(request_type)
        if accepted.get("accepted") is not True:
            raise BridgeError("OBS_MUTATION_REJECTED")
        for attempt in range(self._postcondition_attempts):
            readback = self.recording_status()
            if readback.get("outputActive") is active and readback.get("outputPaused") is paused:
                return {**readback, "verified": True}
            if attempt + 1 < self._postcondition_attempts:
                time.sleep(self._postcondition_poll_seconds)
        raise BridgeError("OBS_POSTCONDITION_FAILED")

    def _request(
        self, request_type: str, data: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        try:
            response = self._transport.vendor_request(request_type, data or {})
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("OBS_CONNECTION_FAILED") from exc
        if not isinstance(response, dict):
            raise BridgeError("OBS_RESPONSE_INVALID")
        if response.get("ok") is False:
            code = response.get("errorCode")
            if not isinstance(code, str) or not code.startswith("OBS_"):
                code = "OBS_REQUEST_FAILED"
            raise BridgeError(code)
        return response

    def _checked(
        self, request_type: str, data: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        response = self._request(request_type, data)
        if self._parse_identity(response) != self._identity:
            raise BridgeError("OBS_INSTANCE_DRIFT")
        return response

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


__all__ = ["BridgeError", "ObsControlBridge", "VendorTransport"]
