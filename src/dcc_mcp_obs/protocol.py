"""Minimal OBS WebSocket 5.x transport for native vendor requests."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol

import websocket

from .config import ObsEndpointConfig

MAX_FRAME_BYTES = 1_048_576
MAX_INTERLEAVED_EVENTS = 64
VENDOR_REQUESTS = frozenset(
    {
        "GetPluginStatus",
        "GetOperatorStatus",
        "RequestGracefulShutdown",
        "CreateAgentInputOverlay",
        "GetAgentInputOverlay",
        "SetAgentInputOverlayLayout",
        "EmitAgentInputActivity",
        "ClearAgentInputOverlay",
        "ListScenes",
        "GetCurrentScene",
        "SetCurrentScene",
        "CreateScene",
        "RenameScene",
        "RemoveScene",
        "ListSources",
        "GetSourceIdentity",
        "CreateSource",
        "RenameSource",
        "RemoveSource",
        "ListInputKinds",
        "GetInputSettings",
        "SetInputSettings",
        "DescribeProperties",
        "ValidatePropertyValue",
        "SetPropertyValue",
        "ListFilters",
        "GetFilter",
        "CreateFilter",
        "SetFilterEnabled",
        "SetFilterSettings",
        "RemoveFilter",
        "GetSourceVolume",
        "SetSourceVolume",
        "GetSourceMute",
        "SetSourceMute",
        "GetSourceMonitorType",
        "SetSourceMonitorType",
        "GetMediaStatus",
        "PlayMedia",
        "PauseMedia",
        "RestartMedia",
        "StopMedia",
        "SeekMedia",
        "ListSceneItems",
        "GetSceneItem",
        "CreateSceneItem",
        "CreateWindowCaptureSource",
        "GetWindowCaptureSource",
        "ListWindowCaptureCandidates",
        "RestoreWindowCaptureCandidate",
        "RebindWindowCaptureSource",
        "SetWindowCaptureMethod",
        "RemoveSceneItem",
        "SetSceneItemEnabled",
        "SetSceneItemTransform",
        "ListTransitions",
        "GetCurrentTransition",
        "SetCurrentTransition",
        "TriggerTransition",
        "GetStudioModeStatus",
        "SetStudioMode",
        "GetCurrentPreviewScene",
        "SetCurrentPreviewScene",
        "TriggerStudioModeTransition",
        "GetRecordingStatus",
        "StartRecording",
        "StopRecording",
        "PauseRecording",
        "ResumeRecording",
        "StartSceneRecordings",
        "GetSceneRecordingSession",
        "StopSceneRecordings",
        "GetStreamingStatus",
        "StartStreaming",
        "StopStreaming",
        "GetReplayBufferStatus",
        "StartReplayBuffer",
        "StopReplayBuffer",
        "SaveReplayBuffer",
        "GetVirtualCameraStatus",
        "StartVirtualCamera",
        "StopVirtualCamera",
        "ListOutputs",
        "GetOutputStatus",
        "StartOutput",
        "StopOutput",
        "ListProfiles",
        "GetCurrentProfile",
        "SetCurrentProfile",
        "ListSceneCollections",
        "GetCurrentSceneCollection",
        "SetCurrentSceneCollection",
        "ListAllowlistedHotkeys",
        "TriggerAllowlistedHotkey",
        "CaptureScreenshot",
        "CaptureSourceScreenshot",
        "CaptureProgramFrame",
    }
)
MUTATING_VENDOR_REQUESTS = frozenset(
    {
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
        "RemoveSceneItem",
        "SetSceneItemEnabled",
        "SetSceneItemTransform",
        "RequestGracefulShutdown",
        "CreateAgentInputOverlay",
        "SetAgentInputOverlayLayout",
        "EmitAgentInputActivity",
        "ClearAgentInputOverlay",
        "StartSceneRecordings",
        "StopSceneRecordings",
        "SetCurrentTransition",
        "TriggerTransition",
        "SetCurrentPreviewScene",
        "SetStudioMode",
        "TriggerStudioModeTransition",
        "TriggerAllowlistedHotkey",
        "CaptureScreenshot",
        "CaptureSourceScreenshot",
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
    }
)


class ProtocolError(RuntimeError):
    """Stable protocol error without server comments or secret material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SocketLike(Protocol):
    def settimeout(self, timeout: float) -> object: ...

    def recv(self) -> str | bytes: ...

    def send(self, payload: str) -> object: ...

    def close(self) -> object: ...


class ObsWebSocketTransport:
    """One authenticated, serialized WebSocket session bound to one endpoint."""

    def __init__(
        self,
        config: ObsEndpointConfig,
        *,
        connector: Callable[[str, float], SocketLike] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._connector = connector or self._connect
        self._socket: SocketLike | None = None
        self._lock = threading.Lock()
        self._clock = clock

    def close(self) -> None:
        with self._lock:
            socket, self._socket = self._socket, None
            if socket is not None:
                with suppress(Exception):
                    socket.close()

    def vendor_request(
        self,
        request_type: str,
        data: Mapping[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        if (
            not isinstance(request_type, str)
            or request_type not in VENDOR_REQUESTS
            or not isinstance(data, Mapping)
        ):
            raise ProtocolError("OBS_REQUEST_INVALID")
        if deadline is None:
            deadline = self._clock() + self._config.timeout_seconds
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ProtocolError("OBS_REQUEST_INVALID")
        remaining = self._remaining(deadline)
        if not self._lock.acquire(timeout=remaining):
            raise ProtocolError("OBS_TIMEOUT")
        try:
            request_sent = False

            def mark_request_sent() -> None:
                nonlocal request_sent
                request_sent = True

            try:
                socket = self._ensure_connected(deadline)
                request_id = uuid.uuid4().hex
                request_payload = dict(data)
                # Carry the caller's absolute wall-clock deadline across the
                # websocket vendor boundary. Native UI work must never
                # reconstruct a fresh budget after this request arrives.
                request_payload["__dccDeadlineAtMs"] = math.ceil(
                    (time.time() + self._remaining(deadline)) * 1000
                )
                self._remaining(deadline)
                self._send_json(
                    socket,
                    {
                        "op": 6,
                        "d": {
                            "requestType": "CallVendorRequest",
                            "requestId": request_id,
                            "requestData": {
                                "vendorName": "dcc-mcp-obs",
                                "requestType": request_type,
                                "requestData": request_payload,
                            },
                        },
                    },
                    deadline,
                    on_send_attempt=mark_request_sent,
                )
                response = self._receive_response(socket, request_id, deadline)
                status = response.get("requestStatus")
                if not isinstance(status, dict) or status.get("result") is not True:
                    raise ProtocolError("OBS_REQUEST_FAILED")
                payload = response.get("responseData")
                if not isinstance(payload, dict):
                    raise ProtocolError("OBS_RESPONSE_INVALID")
                vendor_payload = payload.get("responseData")
                if not isinstance(vendor_payload, dict):
                    raise ProtocolError("OBS_RESPONSE_INVALID")
                return vendor_payload
            except ProtocolError as exc:
                self._disconnect_locked()
                if request_sent and request_type in MUTATING_VENDOR_REQUESTS:
                    raise ProtocolError("OBS_UI_INDETERMINATE") from exc
                raise
            except (TimeoutError, websocket.WebSocketTimeoutException) as exc:
                self._disconnect_locked()
                code = (
                    "OBS_UI_INDETERMINATE"
                    if request_sent and request_type in MUTATING_VENDOR_REQUESTS
                    else "OBS_TIMEOUT"
                )
                raise ProtocolError(code) from exc
            except Exception as exc:
                self._disconnect_locked()
                code = (
                    "OBS_UI_INDETERMINATE"
                    if request_sent and request_type in MUTATING_VENDOR_REQUESTS
                    else "OBS_CONNECTION_FAILED"
                )
                raise ProtocolError(code) from exc
        finally:
            self._lock.release()

    def _ensure_connected(self, deadline: float) -> SocketLike:
        if self._socket is not None:
            return self._socket
        url = f"ws://{self._config.host}:{self._config.port}"
        socket = self._connector(
            url,
            min(self._config.timeout_seconds, self._remaining(deadline)),
        )
        try:
            hello = self._read_json(socket, deadline)
            if hello.get("op") != 0 or not isinstance(hello.get("d"), dict):
                raise ProtocolError("OBS_HANDSHAKE_INVALID")
            details = hello["d"]
            rpc_version = details.get("rpcVersion")
            ws_version = details.get("obsWebSocketVersion")
            if (
                not isinstance(rpc_version, int)
                or isinstance(rpc_version, bool)
                or rpc_version != 1
                or not isinstance(ws_version, str)
                or not ws_version.startswith("5.")
            ):
                raise ProtocolError("OBS_VERSION_UNSUPPORTED")
            identify: dict[str, object] = {"rpcVersion": 1, "eventSubscriptions": 0}
            authentication = details.get("authentication")
            if authentication is not None:
                identify["authentication"] = self._authentication(authentication)
            self._send_json(socket, {"op": 1, "d": identify}, deadline)
            identified = self._read_json(socket, deadline)
            if identified.get("op") != 2:
                raise ProtocolError("OBS_AUTHENTICATION_FAILED")
            identified_details = identified.get("d")
            negotiated_rpc_version = (
                identified_details.get("negotiatedRpcVersion")
                if isinstance(identified_details, dict)
                else None
            )
            if type(negotiated_rpc_version) is not int or negotiated_rpc_version != 1:
                raise ProtocolError("OBS_VERSION_UNSUPPORTED")
        except Exception:
            with suppress(Exception):
                socket.close()
            raise
        self._socket = socket
        return socket

    def _authentication(self, raw: object) -> str:
        if not isinstance(raw, dict):
            raise ProtocolError("OBS_HANDSHAKE_INVALID")
        challenge = raw.get("challenge")
        salt = raw.get("salt")
        if not isinstance(challenge, str) or not challenge or not isinstance(salt, str) or not salt:
            raise ProtocolError("OBS_HANDSHAKE_INVALID")
        secret = base64.b64encode(
            hashlib.sha256((self._config.password + salt).encode("utf-8")).digest()
        ).decode("ascii")
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode("ascii")

    def _receive_response(
        self, socket: SocketLike, request_id: str, deadline: float
    ) -> dict[str, Any]:
        for _ in range(MAX_INTERLEAVED_EVENTS + 1):
            message = self._read_json(socket, deadline)
            if message.get("op") == 5:
                continue
            if message.get("op") != 7 or not isinstance(message.get("d"), dict):
                raise ProtocolError("OBS_RESPONSE_INVALID")
            response = message["d"]
            if response.get("requestId") != request_id:
                raise ProtocolError("OBS_RESPONSE_MISMATCH")
            if response.get("requestType") != "CallVendorRequest":
                raise ProtocolError("OBS_RESPONSE_MISMATCH")
            return response
        raise ProtocolError("OBS_EVENT_OVERFLOW")

    def _read_json(self, socket: SocketLike, deadline: float) -> dict[str, Any]:
        socket.settimeout(self._remaining(deadline))
        raw = socket.recv()
        self._within_deadline(deadline)
        if isinstance(raw, bytes):
            if len(raw) > MAX_FRAME_BYTES:
                raise ProtocolError("OBS_FRAME_TOO_LARGE")
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError("OBS_RESPONSE_INVALID") from exc
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ProtocolError("OBS_FRAME_TOO_LARGE")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("OBS_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise ProtocolError("OBS_RESPONSE_INVALID")
        return value

    def _send_json(
        self,
        socket: SocketLike,
        value: Mapping[str, object],
        deadline: float,
        *,
        on_send_attempt: Callable[[], None] | None = None,
    ) -> None:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ProtocolError("OBS_FRAME_TOO_LARGE")
        socket.settimeout(self._remaining(deadline))
        if on_send_attempt is not None:
            on_send_attempt()
        socket.send(encoded)
        self._within_deadline(deadline)

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ProtocolError("OBS_TIMEOUT")
        return remaining

    def _within_deadline(self, deadline: float) -> None:
        if self._clock() > deadline:
            raise ProtocolError("OBS_TIMEOUT")

    @staticmethod
    def _connect(url: str, timeout: float) -> SocketLike:
        return websocket.create_connection(url, timeout=timeout, enable_multithread=True)

    def _disconnect_locked(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            with suppress(Exception):
                socket.close()


__all__ = ["MAX_FRAME_BYTES", "VENDOR_REQUESTS", "ObsWebSocketTransport", "ProtocolError"]
