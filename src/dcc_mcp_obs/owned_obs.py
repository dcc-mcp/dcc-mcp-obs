"""An OBS child owned by its original Popen, never recovered by PID attachment."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bridge import PUBLIC_DOWNSTREAM_ERRORS, ObsControlBridge
from .config import ObsEndpointConfig
from .protocol import ObsWebSocketTransport

if TYPE_CHECKING:
    from .disposable_obs import DisposableObsLayout

_PUBLIC_OWNER_ERRORS = PUBLIC_DOWNSTREAM_ERRORS | {
    "OBS_OWNED_NOT_STARTED",
    "OBS_OWNED_IDENTITY_MISMATCH",
    "OBS_OWNED_IMAGE_HASH_CHANGED",
    "OBS_OWNED_AUTHENTICATION_REQUIRED",
    "OBS_OWNED_NATIVE_PID_MISMATCH",
    "OBS_OWNED_NATIVE_INSTANCE_INVALID",
    "OBS_OWNED_WAIT_RESULT_INVALID",
    "OBS_OWNED_NOT_READY",
    "OBS_OWNED_SHUTDOWN_REJECTED",
    "OBS_ACCEPTANCE_READINESS_TIMEOUT",
    "OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED",
    "OBS_ACCEPTANCE_HOST_BINDING_FAILED",
    "OBS_ACCEPTANCE_ADAPTER_ALREADY_OWNED",
    "OBS_DISPOSABLE_HOST_EXITED",
    "OBS_DISPOSABLE_HOST_TIMEOUT",
    "OBS_DISPOSABLE_HOST_EXIT_FAILED",
    "OBS_ACCEPTANCE_HOST_EXIT_FAILED",
    "OBS_OWNED_MODULE_QUERY_FAILED",
}


@dataclass(frozen=True)
class HandleIdentity:
    pid: int
    creation_filetime: int
    image: str


def windows_handle_identity(process: Any) -> HandleIdentity:
    """Query the original Popen handle; do not open another handle from a PID."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = wintypes.HANDLE(int(process._handle))
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    pid = kernel.GetProcessId(handle)
    times = [wintypes.FILETIME() for _ in range(4)]
    image = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(image))
    if (
        not pid
        or not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
        or not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return HandleIdentity(int(pid), birth, image.value)


def windows_module_paths(process: Any) -> list[Path]:
    """Inspect loaded artifacts through the same original process handle."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = wintypes.HANDLE(int(process._handle))
    kernel.K32EnumProcessModulesEx.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HMODULE),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    kernel.K32EnumProcessModulesEx.restype = wintypes.BOOL
    kernel.K32GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE,
        wintypes.HMODULE,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel.K32GetModuleFileNameExW.restype = wintypes.DWORD
    count = 1024
    for _ in range(3):
        modules = (wintypes.HMODULE * count)()
        needed = wintypes.DWORD()
        if not kernel.K32EnumProcessModulesEx(
            handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value <= ctypes.sizeof(modules):
            paths = []
            for module in modules[: needed.value // ctypes.sizeof(wintypes.HMODULE)]:
                buffer = ctypes.create_unicode_buffer(32768)
                length = kernel.K32GetModuleFileNameExW(handle, module, buffer, len(buffer))
                if not length:
                    raise ctypes.WinError(ctypes.get_last_error())
                if length >= len(buffer):
                    raise RuntimeError("OBS_OWNED_MODULE_QUERY_FAILED")
                paths.append(Path(buffer.value))
            return paths
        count = needed.value // ctypes.sizeof(wintypes.HMODULE) + 64
        if count > 65536:
            break
    raise RuntimeError("OBS_OWNED_MODULE_QUERY_FAILED")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class OwnedObsError(RuntimeError):
    """The caller must retain owner until its original wait proves termination."""

    def __init__(self, code: str, owner: OwnedObsHost, primary_error: BaseException) -> None:
        super().__init__(code)
        self.owner = owner
        self.primary_error = primary_error


class OwnedObsHost:
    """One child, one authenticated bridge, and one terminal shutdown attempt.

    Failed operations retain the original Popen here and on OwnedObsError.owner.
    Evidence files are local diagnostics, not a credential-bearing connection API.
    """

    def __init__(
        self,
        layout: DisposableObsLayout,
        config: ObsEndpointConfig,
        *,
        platform: str,
        identity_reader: Callable[[Any], HandleIdentity] = windows_handle_identity,
    ) -> None:
        if not config.password or config.host != "127.0.0.1" or config.secure:
            raise ValueError("OBS_OWNED_AUTHENTICATED_LOOPBACK_REQUIRED")
        self.layout = layout
        self.config = config
        self.platform = platform
        self._identity_reader = identity_reader
        self.process: Any = None
        self.identity: HandleIdentity | None = None
        self.executable = layout.executable.resolve(strict=True)
        if not layout.command or Path(layout.command[0]).resolve(strict=True) != self.executable:
            raise ValueError("OBS_OWNED_COMMAND_IMAGE_MISMATCH")
        root = layout.root.resolve(strict=True)
        if (
            not layout.config_root.resolve().is_relative_to(root)
            or not layout.recordings.resolve().is_relative_to(root)
            or (
                platform == "windows"
                and (
                    not self.executable.is_relative_to(root)
                    or "--portable" not in layout.command
                    or "--multi" not in layout.command
                )
            )
        ):
            raise ValueError("OBS_OWNED_PRIVATE_LAYOUT_REQUIRED")
        self.executable_sha256 = _sha256(self.executable)
        self.transport: Any = None
        self._transport_closed = False
        self.bridge: Any = None
        self.instance_id: str | None = None
        self.ready = False
        self.shutdown_attempted = False
        self.returncode: int | None = None
        self.primary_error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.evidence_path = layout.root / "owned-obs-lifecycle.json"
        self.stdout_path = layout.root / "obs-stdout.log"
        self.stderr_path = layout.root / "obs-stderr.log"

    def _record(self) -> None:
        payload = {
            "state": "TERMINAL"
            if self.returncode is not None
            else "READY"
            if self.ready
            else "UNKNOWN"
            if self.primary_error
            else "STARTING",
            "hostPid": self.process.pid if self.process is not None else None,
            "identity": asdict(self.identity) if self.identity else None,
            "executable": str(self.executable),
            "executableSha256": self.executable_sha256,
            "candidateWebsocketPort": self.config.port,
            "instanceId": self.instance_id,
            "privateRoot": str(self.layout.root),
            "sceneCollection": "DCC Acceptance",
            "profile": "DCC Acceptance",
            "shutdownAttempted": self.shutdown_attempted,
            "originalWaitReturncode": self.returncode,
            "stdout": str(self.stdout_path),
            "stderr": str(self.stderr_path),
            "primaryErrorType": type(self.primary_error).__name__ if self.primary_error else None,
            "primaryWinerror": getattr(self.primary_error, "winerror", None),
            "primaryErrorCode": (
                self.primary_error.args[0]
                if self.primary_error
                and self.primary_error.args
                and isinstance(self.primary_error.args[0], str)
                and self.primary_error.args[0] in _PUBLIC_OWNER_ERRORS
                else None
            ),
            "cleanupErrorTypes": [type(error).__name__ for error in self.cleanup_errors],
        }
        self.evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def failure(self, code: str, error: BaseException) -> OwnedObsError:
        self.ready = False
        if self.primary_error is None:
            self.primary_error = error
        try:
            self._record()
        except BaseException as evidence_error:
            self.cleanup_errors.append(evidence_error)
        return OwnedObsError(code, self, self.primary_error)

    def verify_identity(self) -> None:
        if self.process is None:
            raise RuntimeError("OBS_OWNED_NOT_STARTED")
        if self.platform == "windows":
            observed = self._identity_reader(self.process)
            if (
                observed.pid != self.process.pid
                or type(observed.creation_filetime) is not int
                or not 0 < observed.creation_filetime < 2**64
                or Path(observed.image).resolve(strict=True) != self.executable
                or (self.identity is not None and observed != self.identity)
            ):
                raise RuntimeError("OBS_OWNED_IDENTITY_MISMATCH")
            self.identity = observed
        if _sha256(self.executable) != self.executable_sha256:
            raise RuntimeError("OBS_OWNED_IMAGE_HASH_CHANGED")

    def mapped_module_paths(self) -> list[Path]:
        if self.platform != "windows":
            raise RuntimeError("OBS_OWNED_MODULE_QUERY_UNSUPPORTED")
        self.verify_identity()
        paths = windows_module_paths(self.process)
        self.verify_identity()
        return paths

    def start(
        self,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        readiness_probe: Callable[[Any, int], None] | None = None,
        transport_factory: Callable[..., Any] = ObsWebSocketTransport,
        bridge_factory: Callable[..., Any] = ObsControlBridge,
    ) -> OwnedObsHost:
        from .disposable_obs import _wait_for_obs
        from .real_obs_acceptance import _probe_authentication_required, await_authenticated_status

        if self.process is not None:
            raise self.failure("OBS_OWNED_ALREADY_STARTED", RuntimeError("already started"))
        child_environment = dict(os.environ)
        child_environment.update(self.layout.environment)
        # This Python owner supplies the adapter; the native launcher must stay disabled.
        for name in (
            "DCC_MCP_OBS_EXECUTABLE",
            "DCC_MCP_OBS_WEBSOCKET_PASSWORD",
            "DCC_MCP_OBS_WEBSOCKET_URL",
        ):
            child_environment.pop(name, None)
        try:
            if _sha256(self.executable) != self.executable_sha256:
                raise RuntimeError("OBS_OWNED_IMAGE_HASH_CHANGED")
            with self.stdout_path.open("xb") as stdout, self.stderr_path.open("xb") as stderr:
                self.process = popen_factory(
                    self.layout.command,
                    cwd=self.executable.parent,
                    env=child_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                )
            self.verify_identity()
            self._record()
            (readiness_probe or _wait_for_obs)(self.process, self.config.port)
            if not _probe_authentication_required(self.config):
                raise RuntimeError("OBS_OWNED_AUTHENTICATION_REQUIRED")
            self.transport = transport_factory(self.config)
            self.bridge = bridge_factory(self.transport, expected_pid=self.process.pid)
            status = await_authenticated_status(self.bridge.status)
            if status.get("hostPid") != self.process.pid:
                raise RuntimeError("OBS_OWNED_NATIVE_PID_MISMATCH")
            self.verify_identity()
            instance_id = status.get("instanceId")
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise RuntimeError("OBS_OWNED_NATIVE_INSTANCE_INVALID")
            self.instance_id = instance_id
            self.ready = True
            self._record()
            return self
        except BaseException as error:
            raise self.failure("OBS_OWNED_START_FAILED", error) from error

    def wait_for_exit(self, *, timeout: float | None = 15) -> int:
        """Only the original Popen.wait establishes terminal state, including after failure."""
        if self.process is None:
            raise self.failure("OBS_OWNED_NOT_STARTED", RuntimeError("not started"))
        if self.returncode is not None:
            return self.returncode
        try:
            code = self.process.wait(timeout=timeout)
            if type(code) is not int:
                raise RuntimeError("OBS_OWNED_WAIT_RESULT_INVALID")
            self.returncode = code
            self.ready = False
            self._record()
            return self.returncode
        except BaseException as error:
            raise self.failure("OBS_OWNED_TERMINATION_UNKNOWN", error) from error

    def close(
        self,
        *,
        shutdown: Callable[[], Mapping[str, object]] | None = None,
        close_adapter: Callable[[], None] | None = None,
        timeout: float = 15,
    ) -> int:
        """Submit once, disconnect clients, then wait on the original child handle."""
        if self.shutdown_attempted:
            raise self.failure(
                "OBS_OWNED_SHUTDOWN_ALREADY_ATTEMPTED", RuntimeError("already attempted")
            )
        error: BaseException | None = None
        self.shutdown_attempted = True
        try:
            self.verify_identity()
            if not self.ready or self.bridge is None:
                raise RuntimeError("OBS_OWNED_NOT_READY")
            response = (shutdown or self.bridge.request_graceful_shutdown)()
            context = response.get("context", response)
            if (
                not isinstance(context, Mapping)
                or context.get("hostPid") != self.process.pid
                or context.get("instanceId") != self.instance_id
                or context.get("shutdownScheduled") is not True
                or (shutdown is not None and response.get("success") is not True)
            ):
                raise RuntimeError("OBS_OWNED_SHUTDOWN_REJECTED")
        except BaseException as primary:
            error = primary
            if self.primary_error is None:
                self.primary_error = primary
        finally:
            for closer in (close_adapter, self.disconnect):
                if closer is not None:
                    try:
                        closer()
                    except BaseException as cleanup:
                        self.cleanup_errors.append(cleanup)
                        if error is None:
                            error = cleanup
                            if self.primary_error is None:
                                self.primary_error = cleanup
            self.ready = False
        try:
            code = self.wait_for_exit(timeout=timeout)
        except OwnedObsError as wait_error:
            if error is None:
                error = wait_error.primary_error
        if error is not None:
            raise self.failure("OBS_OWNED_CLOSE_FAILED", error) from error
        return code

    def disconnect(self) -> None:
        if self.transport is not None and not self._transport_closed:
            self.transport.close()
            self._transport_closed = True

    def retain_until_exit(self) -> None:
        """CLI failure path: retain ownership even if shutdown or an interrupt failed."""
        try:
            self.disconnect()
        except BaseException as error:
            self.cleanup_errors.append(error)
        while self.process is not None and self.returncode is None:
            try:
                self.wait_for_exit(timeout=None)
            except OwnedObsError:
                # An interrupted wait is not process termination or permission to kill it.
                with suppress(KeyboardInterrupt):
                    time.sleep(0.25)
                continue


__all__ = ["HandleIdentity", "OwnedObsError", "OwnedObsHost", "windows_handle_identity"]
