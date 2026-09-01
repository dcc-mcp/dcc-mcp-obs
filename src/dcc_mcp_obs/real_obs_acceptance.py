"""Fail-closed contracts for disposable, real-OBS release acceptance."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import importlib.metadata
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import NoReturn, Protocol

from .__version__ import __version__
from .bridge import ObsControlBridge
from .config import ObsEndpointConfig
from .process import OBS_EXECUTABLES
from .protocol import ObsWebSocketTransport

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
_ARCHITECTURE_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")
_PLATFORMS = frozenset({"windows", "macos", "linux"})
_RECORDING_FLOW = ("stopped", "recording", "paused", "recording", "stopped")
_MAX_ACCEPTANCE_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_ACCEPTANCE_MEMBERS = 1024
_MAX_ACCEPTANCE_MEMBER_BYTES = 32 * 1024 * 1024


class AcceptanceContractError(RuntimeError):
    """Stable public failure raised when live acceptance evidence is incomplete."""


class AcceptanceToolClient(Protocol):
    """One initialized MCP session for the installed OBS adapter."""

    session_id: str

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]: ...


class McpAcceptanceClient:
    """Small Streamable-HTTP client that preserves one exact MCP session."""

    def __init__(self, url: str, *, session_id: str, timeout_seconds: float = 30.0) -> None:
        if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
            _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
        if not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 60:
            _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
        if not isinstance(session_id, str) or not session_id:
            _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
        self._url = url
        self._timeout_seconds = float(timeout_seconds)
        self._request_id = 0
        self.session_id = session_id
        self._http_session_id = ""

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._http_session_id:
            headers["Mcp-Session-Id"] = self._http_session_id
        request = urllib.request.Request(
            self._url,
            json.dumps(payload, separators=(",", ":")).encode(),
            headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                observed_session = response.headers.get("Mcp-Session-Id")
                if self._http_session_id:
                    if observed_session is not None and observed_session != self._http_session_id:
                        _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
                elif observed_session:
                    self._http_session_id = observed_session
                body = response.read().decode("utf-8").strip()
        except AcceptanceContractError:
            raise
        except (OSError, UnicodeError, urllib.error.URLError) as exc:
            raise AcceptanceContractError("OBS_ACCEPTANCE_ADAPTER_CONNECTION_FAILED") from exc
        if not body:
            return {}
        if body.startswith("event:") or "\ndata: " in body:
            try:
                body = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
            except StopIteration as exc:
                raise AcceptanceContractError("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AcceptanceContractError("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID") from exc
        if not isinstance(parsed, dict) or "error" in parsed:
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        return parsed

    def initialize(self) -> None:
        initialized = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "dcc-mcp-obs-acceptance", "version": __version__},
                },
            }
        )
        if not isinstance(initialized.get("result"), Mapping):
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        discovered = self._raw_tools_call("search_skills", {"query": "OBS recording scenes"})
        if "obs-control" not in json.dumps(discovered, sort_keys=True):
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        loaded = self._raw_tools_call("load_skill", {"skill_name": "obs-control"})
        if "obs_control__get_status" not in json.dumps(loaded, sort_keys=True):
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")

    def _raw_tools_call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        return dict(result)

    def _tools_call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._raw_tools_call(name, arguments)
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        return dict(structured)

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        envelope = self._tools_call(name, arguments)
        job_id = envelope.get("job_id")
        if job_id is None:
            return envelope
        if not isinstance(job_id, str) or not job_id:
            _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            job = self._tools_call("jobs_get_status", {"job_id": job_id, "include_result": True})
            status = job.get("status")
            if status == "completed":
                result = job.get("result")
                if not isinstance(result, Mapping):
                    _fail("OBS_ACCEPTANCE_ADAPTER_RESPONSE_INVALID")
                return dict(result)
            if status in {"failed", "cancelled", "interrupted"}:
                stable_codes = re.findall(r"OBS_[A-Z0-9_]+", json.dumps(job, sort_keys=True))
                suffix = stable_codes[0] if stable_codes else "UNKNOWN"
                _fail(f"OBS_ACCEPTANCE_JOB_FAILED_{suffix}")
            time.sleep(0.05)
        _fail("OBS_ACCEPTANCE_TOOL_TIMEOUT")


def _fail(code: str) -> NoReturn:
    raise AcceptanceContractError(code)


def _required_bool(observed: Mapping[str, object], field: str, code: str) -> None:
    if observed.get(field) is not True:
        _fail(code)


def _required_text(
    observed: Mapping[str, object], field: str, pattern: re.Pattern[str], code: str
) -> str:
    value = observed.get(field)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _required_digest(observed: Mapping[str, object], field: str) -> str:
    return _required_text(observed, field, _SHA256_PATTERN, "OBS_ACCEPTANCE_DIGEST_INVALID")


def _fingerprint(salt: bytes, domain: bytes, value: object) -> str:
    return hashlib.sha256(salt + b"\0" + domain + b"\0" + str(value).encode()).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        _fail("OBS_ACCEPTANCE_ARTIFACT_INVALID")
    return value


def _read_bounded_member(package: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    mode = member.external_attr >> 16
    if (
        member.is_dir()
        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
        or member.file_size > _MAX_ACCEPTANCE_MEMBER_BYTES
        or (member.file_size and member.compress_size <= 0)
        or (member.file_size and member.file_size > member.compress_size * 100)
    ):
        _fail("OBS_ACCEPTANCE_ARTIFACT_INVALID")
    payload = package.read(member)
    if len(payload) != member.file_size:
        _fail("OBS_ACCEPTANCE_ARTIFACT_INVALID")
    return payload


def verify_loaded_native_plugin(archive: Path, *, mapped_files: Sequence[Path]) -> dict[str, str]:
    """Bind one mapped native module to the exact canonical plugin archive."""

    try:
        archive = archive.resolve(strict=True)
        if not archive.is_file() or archive.stat().st_size > _MAX_ACCEPTANCE_ARCHIVE_BYTES:
            _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if not 2 <= len(members) <= _MAX_ACCEPTANCE_MEMBERS:
                _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
            by_name = {_safe_zip_name(member.filename): member for member in members}
            if len(by_name) != len(members) or "dcc-mcp-obs-plugin.json" not in by_name:
                _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
            manifest_payload = _read_bounded_member(package, by_name["dcc-mcp-obs-plugin.json"])
            manifest = json.loads(manifest_payload)
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 1
                or manifest.get("product") != "dcc-mcp-obs"
                or manifest.get("platform") not in _PLATFORMS
                or not isinstance(manifest.get("version"), str)
                or _VERSION_PATTERN.fullmatch(manifest["version"]) is None
                or not isinstance(manifest.get("files"), list)
                or not manifest["files"]
            ):
                _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
            expected_sources: set[str] = set()
            binaries: list[tuple[str, str, str]] = []
            for entry in manifest["files"]:
                if not isinstance(entry, dict) or set(entry) != {"source", "target", "sha256"}:
                    _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
                source = _safe_zip_name(entry["source"])
                target = _safe_zip_name(entry["target"])
                digest = entry["sha256"]
                if (
                    not source.startswith("payload/")
                    or source in expected_sources
                    or not isinstance(digest, str)
                    or _SHA256_PATTERN.fullmatch(digest) is None
                    or source not in by_name
                    or hashlib.sha256(_read_bounded_member(package, by_name[source])).hexdigest()
                    != digest
                ):
                    _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
                expected_sources.add(source)
                basename = target.rsplit("/", 1)[-1]
                if basename.casefold() in {
                    "dcc-mcp-obs.dll",
                    "dcc-mcp-obs.so",
                    "dcc-mcp-obs",
                }:
                    binaries.append((source, target, digest))
            if set(by_name) != {"dcc-mcp-obs-plugin.json", *expected_sources} or len(binaries) != 1:
                _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
            binary_name = binaries[0][1].rsplit("/", 1)[-1]
            matches = [
                Path(path)
                for path in mapped_files
                if Path(path).name.casefold() == binary_name.casefold()
            ]
            if len(matches) != 1 or _sha256_path(matches[0]) != binaries[0][2]:
                _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
    except AcceptanceContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH") from exc
    return {
        "version": manifest["version"],
        "platform": manifest["platform"],
        "archive_sha256": _sha256_path(archive),
        "binary_sha256": binaries[0][2],
    }


def verify_installed_python_wheel(
    wheel: Path,
    *,
    package_root: Path,
    distribution_getter: Callable[[str], object] = importlib.metadata.distribution,
) -> dict[str, str]:
    """Prove that the running package files came from the supplied wheel."""

    try:
        wheel = wheel.resolve(strict=True)
        package_root = package_root.resolve(strict=True)
        if not wheel.is_file() or wheel.stat().st_size > _MAX_ACCEPTANCE_ARCHIVE_BYTES:
            _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
        with zipfile.ZipFile(wheel) as package:
            members = package.infolist()
            if not 2 <= len(members) <= _MAX_ACCEPTANCE_MEMBERS:
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            by_name = {_safe_zip_name(member.filename): member for member in members}
            if len(by_name) != len(members):
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            metadata_names = [name for name in by_name if name.endswith(".dist-info/METADATA")]
            package_names = [
                name
                for name in by_name
                if name.startswith("dcc_mcp_obs/") and not by_name[name].is_dir()
            ]
            if len(metadata_names) != 1 or "dcc_mcp_obs/__init__.py" not in package_names:
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            metadata = email.parser.BytesParser().parsebytes(
                _read_bounded_member(package, by_name[metadata_names[0]])
            )
            if metadata.get("Name", "").casefold() != "dcc-mcp-obs":
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            version = metadata.get("Version")
            if not isinstance(version, str) or _VERSION_PATTERN.fullmatch(version) is None:
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            distribution = distribution_getter("dcc-mcp-obs")
            distribution_metadata = getattr(distribution, "metadata", None)
            distribution_files = getattr(distribution, "files", None)
            if (
                distribution_metadata is None
                or distribution_metadata.get("Name", "").casefold() != "dcc-mcp-obs"
                or getattr(distribution, "version", None) != version
                or not isinstance(distribution_files, list)
            ):
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            installed_inventory = {Path(value).as_posix() for value in distribution_files}
            if not set(package_names).issubset(installed_inventory):
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            located_root = Path(distribution.locate_file("dcc_mcp_obs")).resolve(strict=True)
            if located_root != package_root:
                _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            direct_url = distribution.read_text("direct_url.json")
            if direct_url is not None:
                direct_url_data = json.loads(direct_url)
                if not isinstance(direct_url_data, dict) or (
                    isinstance(direct_url_data.get("dir_info"), dict)
                    and direct_url_data["dir_info"].get("editable") is True
                ):
                    _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
            for name in package_names:
                relative = Path(*name.split("/")[1:])
                installed = package_root / relative
                if (
                    not installed.is_file()
                    or installed.is_symlink()
                    or _sha256_path(installed)
                    != hashlib.sha256(_read_bounded_member(package, by_name[name])).hexdigest()
                ):
                    _fail("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH")
    except AcceptanceContractError:
        raise
    except (
        OSError,
        AttributeError,
        TypeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH") from exc
    return {"version": version, "wheel_sha256": _sha256_path(wheel)}


def _validate_recording(observed: Mapping[str, object]) -> tuple[str, int]:
    states = observed.get("recording_states")
    if not isinstance(states, list) or tuple(states) != _RECORDING_FLOW:
        _fail("OBS_ACCEPTANCE_RECORDING_FLOW_FAILED")
    _required_bool(observed, "recording_output_finalized", "OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")
    raw_path = observed.get("recording_output_path")
    size = observed.get("recording_size_bytes")
    if not isinstance(raw_path, str) or not raw_path or not isinstance(size, int) or size <= 0:
        _fail("OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")
    path = Path(raw_path)
    try:
        if not path.is_file() or path.stat().st_size != size:
            _fail("OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")
        digest = _sha256_path(path)
    except OSError:
        _fail("OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")
    if digest != _required_digest(observed, "recording_sha256"):
        _fail("OBS_ACCEPTANCE_OUTPUT_DIGEST_MISMATCH")
    return digest, size


class _ExactIdentity:
    def __init__(self, *, host_pid: int) -> None:
        self.host_pid = host_pid
        self.instance_id: str | None = None
        self.plugin_version: str | None = None
        self.obs_version: str | None = None

    def observe(self, context: Mapping[str, object]) -> None:
        if context.get("hostPid") != self.host_pid:
            _fail("OBS_ACCEPTANCE_HOST_BINDING_FAILED")
        instance_id = context.get("instanceId")
        plugin_version = context.get("pluginVersion")
        obs_version = context.get("obsVersion")
        if not isinstance(instance_id, str) or not instance_id:
            _fail("OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED")
        if (
            not isinstance(plugin_version, str)
            or _VERSION_PATTERN.fullmatch(plugin_version) is None
            or not isinstance(obs_version, str)
            or _VERSION_PATTERN.fullmatch(obs_version) is None
        ):
            _fail("OBS_ACCEPTANCE_VERSION_INVALID")
        if self.instance_id is None:
            self.instance_id = instance_id
            self.plugin_version = plugin_version
            self.obs_version = obs_version
        elif (
            instance_id != self.instance_id
            or plugin_version != self.plugin_version
            or obs_version != self.obs_version
        ):
            _fail("OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED")


def _call_tool(
    client: AcceptanceToolClient,
    identity: _ExactIdentity,
    name: str,
    arguments: dict[str, object] | None = None,
    *,
    verified: bool = False,
) -> dict[str, object]:
    failure_code = f"OBS_ACCEPTANCE_TOOL_FAILED_{name.upper()}"
    try:
        envelope = client.call(name, arguments or {})
    except Exception as exc:
        nested = str(exc)
        if nested.startswith("OBS_ACCEPTANCE_JOB_FAILED_"):
            failure_code = f"{failure_code}_{nested}"
        raise AcceptanceContractError(failure_code) from exc
    if not isinstance(envelope, Mapping) or envelope.get("success") is not True:
        stable_codes = re.findall(r"OBS_[A-Z0-9_]+", json.dumps(envelope, sort_keys=True))
        if stable_codes:
            failure_code = f"{failure_code}_{stable_codes[0]}"
        _fail(failure_code)
    context = envelope.get("context")
    if not isinstance(context, Mapping):
        _fail(failure_code)
    identity.observe(context)
    if verified:
        postcondition = envelope.get("postcondition")
        if not isinstance(postcondition, Mapping) or postcondition.get("verified") is not True:
            _fail("OBS_ACCEPTANCE_POSTCONDITION_FAILED")
    return dict(context)


def _recording_state(context: Mapping[str, object]) -> str:
    active = context.get("outputActive")
    paused = context.get("outputPaused")
    if type(active) is not bool or type(paused) is not bool or (paused and not active):
        _fail("OBS_ACCEPTANCE_RECORDING_FLOW_FAILED")
    if not active:
        return "stopped"
    return "paused" if paused else "recording"


def _wait_for_finalized_output(
    path: Path,
    *,
    sleeper: Callable[[float], None],
    attempts: int = 40,
) -> tuple[int, str]:
    previous_size = -1
    stable_samples = 0
    for _ in range(attempts):
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        if size > 0 and size == previous_size:
            stable_samples += 1
            if stable_samples >= 2:
                return size, _sha256_path(path)
        else:
            stable_samples = 0
        previous_size = size
        sleeper(0.25)
    _fail("OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")


def exercise_live_obs(
    client: AcceptanceToolClient,
    *,
    host_pid: int,
    platform: str,
    architecture: str,
    native_plugin_sha256: str,
    python_wheel_sha256: str,
    output_root: Path,
    authenticated: bool,
    loaded_native_artifact_verified: bool,
    installed_python_wheel_verified: bool,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Exercise issues #4 and #6 through one exact installed-adapter session."""

    if not isinstance(client.session_id, str) or not client.session_id:
        _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
    identity = _ExactIdentity(host_pid=host_pid)
    baseline_scene = "DCC Acceptance Baseline"
    program_scene = "DCC Acceptance Program"
    preview_scene = "DCC Acceptance Preview"
    source_name = "DCC Acceptance Color"

    status = _call_tool(client, identity, "obs_control__get_status")
    if status.get("ready") is not True:
        _fail("OBS_ACCEPTANCE_AUTHENTICATION_REQUIRED")
    for scene_name in (baseline_scene, program_scene, preview_scene):
        _call_tool(
            client,
            identity,
            "obs_control__create_scene",
            {"scene_name": scene_name},
            verified=True,
        )

    # Select the transition while the host is idle. A preceding scene switch
    # can still be transitioning for its configured duration, during which OBS
    # legitimately defers a transition-source swap.
    transitions = _call_tool(client, identity, "obs_control__list_transitions")
    values = transitions.get("transitions")
    if not isinstance(values, list) or not values:
        _fail("OBS_ACCEPTANCE_TRANSITION_FAILED")
    current_transition = transitions.get("currentTransitionName")
    transition_names = [
        value.get("transitionName")
        for value in values
        if isinstance(value, Mapping)
        and isinstance(value.get("transitionName"), str)
        and value["transitionName"]
    ]
    transition_name = next(
        (value for value in transition_names if value != current_transition),
        transition_names[0] if transition_names else None,
    )
    if not isinstance(transition_name, str) or not transition_name:
        _fail("OBS_ACCEPTANCE_TRANSITION_FAILED")
    _call_tool(
        client,
        identity,
        "obs_control__set_current_transition",
        {"transition_name": transition_name},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__set_current_scene",
        {"scene_name": program_scene},
        verified=True,
    )
    sleeper(0.5)
    _call_tool(
        client,
        identity,
        "obs_control__create_source",
        {
            "scene_name": program_scene,
            "source_name": source_name,
            "source_kind": "color_source_v3",
            "schema_version": "1.0",
            "settings": {"width": 640, "height": 360, "color": 4_278_255_360},
            "enabled": True,
        },
        verified=True,
    )
    scene_items = _call_tool(
        client,
        identity,
        "obs_control__list_scene_items",
        {"scene_name": program_scene},
    )
    matches = [
        item
        for item in scene_items.get("sceneItems", [])
        if isinstance(item, Mapping)
        and item.get("sourceName") == source_name
        and item.get("sourceKind") == "color_source_v3"
    ]
    if len(matches) != 1 or type(matches[0].get("sceneItemId")) is not int:
        _fail("OBS_ACCEPTANCE_SCENE_GRAPH_FAILED")
    scene_item_id = matches[0]["sceneItemId"]
    _call_tool(
        client,
        identity,
        "obs_control__set_scene_item_transform",
        {
            "scene_name": program_scene,
            "scene_item_id": scene_item_id,
            "position": [16.0, 12.0],
            "scale": [0.95, 0.95],
            "rotation": 1.0,
        },
        verified=True,
    )

    _call_tool(
        client,
        identity,
        "obs_control__trigger_transition",
        {"scene_name": preview_scene},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__set_studio_mode",
        {"enabled": True},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__set_preview_scene",
        {"scene_name": program_scene},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__transition_to_program",
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__set_studio_mode",
        {"enabled": False},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__set_current_scene",
        {"scene_name": program_scene},
        verified=True,
    )
    frame = _call_tool(client, identity, "obs_control__capture_program_frame")
    if (
        frame.get("imageWidth") != 320
        or frame.get("imageHeight") != 180
        or type(frame.get("byteLength")) is not int
        or frame["byteLength"] <= 0
        or not isinstance(frame.get("sha256"), str)
        or _SHA256_PATTERN.fullmatch(frame["sha256"]) is None
    ):
        _fail("OBS_ACCEPTANCE_SOURCE_READBACK_FAILED")

    recording_states = [
        _recording_state(_call_tool(client, identity, "obs_control__get_recording_status"))
    ]
    recording_states.append(
        _recording_state(
            _call_tool(client, identity, "obs_control__start_recording", verified=True)
        )
    )
    sleeper(0.75)
    recording_states.append(
        _recording_state(
            _call_tool(client, identity, "obs_control__pause_recording", verified=True)
        )
    )
    sleeper(0.25)
    recording_states.append(
        _recording_state(
            _call_tool(client, identity, "obs_control__resume_recording", verified=True)
        )
    )
    sleeper(0.75)
    stopped = _call_tool(client, identity, "obs_control__stop_recording", verified=True)
    recording_states.append(_recording_state(stopped))
    if tuple(recording_states) != _RECORDING_FLOW:
        _fail("OBS_ACCEPTANCE_RECORDING_FLOW_FAILED")
    output_value = stopped.get("outputPath")
    if not isinstance(output_value, str) or not output_value:
        _fail("OBS_ACCEPTANCE_OUTPUT_NOT_FINALIZED")
    output_path = Path(output_value).resolve()
    try:
        output_path.relative_to(output_root.resolve())
    except (OSError, ValueError):
        _fail("OBS_ACCEPTANCE_OUTPUT_OUTSIDE_DISPOSABLE_ROOT")
    output_size, output_digest = _wait_for_finalized_output(output_path, sleeper=sleeper)

    _call_tool(
        client,
        identity,
        "obs_control__set_current_scene",
        {"scene_name": baseline_scene},
        verified=True,
    )
    _call_tool(
        client,
        identity,
        "obs_control__remove_scene_item",
        {"scene_name": program_scene, "scene_item_id": scene_item_id},
        verified=True,
    )
    remaining_sources = _call_tool(client, identity, "obs_control__list_sources")
    if any(
        isinstance(source, Mapping) and source.get("sourceName") == source_name
        for source in remaining_sources.get("sources", [])
    ):
        _fail("OBS_ACCEPTANCE_SOURCE_CLEANUP_FAILED")
    for scene_name in (preview_scene, program_scene):
        _call_tool(
            client,
            identity,
            "obs_control__remove_scene",
            {"scene_name": scene_name},
            verified=True,
        )

    if (
        identity.instance_id is None
        or identity.plugin_version is None
        or identity.obs_version is None
    ):
        _fail("OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED")
    if client.session_id != identity.instance_id:
        _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
    return {
        "platform": platform,
        "architecture": architecture,
        "obs_version": identity.obs_version,
        "plugin_version": identity.plugin_version,
        "adapter_version": __version__,
        "native_plugin_sha256": native_plugin_sha256,
        "python_wheel_sha256": python_wheel_sha256,
        "loaded_native_artifact_verified": loaded_native_artifact_verified,
        "installed_python_wheel_verified": installed_python_wheel_verified,
        "host_pid": host_pid,
        "plugin_instance_id": identity.instance_id,
        "adapter_session_id": client.session_id,
        "authenticated": authenticated,
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
        "recording_states": recording_states,
        "recording_output_path": str(output_path),
        "recording_output_finalized": True,
        "recording_sha256": output_digest,
        "recording_size_bytes": output_size,
    }


def build_public_evidence(observed: Mapping[str, object], *, salt: bytes) -> dict[str, object]:
    """Validate live observations and return a privacy-safe public report.

    Raw process IDs, plugin instance IDs, adapter session IDs, passwords, ports,
    and filesystem paths are deliberately excluded. Exact binding is retained as
    a per-run salted fingerprint so a public report can prove internal equality
    without becoming host inventory.
    """

    if not isinstance(salt, bytes) or len(salt) < 16:
        _fail("OBS_ACCEPTANCE_SALT_INVALID")
    platform = observed.get("platform")
    if platform not in _PLATFORMS:
        _fail("OBS_ACCEPTANCE_PLATFORM_INVALID")
    architecture = _required_text(
        observed,
        "architecture",
        _ARCHITECTURE_PATTERN,
        "OBS_ACCEPTANCE_PLATFORM_INVALID",
    )
    versions = {
        "obs": _required_text(
            observed, "obs_version", _VERSION_PATTERN, "OBS_ACCEPTANCE_VERSION_INVALID"
        ),
        "nativePlugin": _required_text(
            observed, "plugin_version", _VERSION_PATTERN, "OBS_ACCEPTANCE_VERSION_INVALID"
        ),
        "pythonAdapter": _required_text(
            observed, "adapter_version", _VERSION_PATTERN, "OBS_ACCEPTANCE_VERSION_INVALID"
        ),
    }
    host_pid = observed.get("host_pid")
    plugin_instance_id = observed.get("plugin_instance_id")
    adapter_session_id = observed.get("adapter_session_id")
    if not isinstance(host_pid, int) or host_pid <= 0:
        _fail("OBS_ACCEPTANCE_HOST_BINDING_FAILED")
    if not isinstance(plugin_instance_id, str) or not plugin_instance_id:
        _fail("OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED")
    if not isinstance(adapter_session_id, str) or not adapter_session_id:
        _fail("OBS_ACCEPTANCE_SESSION_BINDING_FAILED")

    _required_bool(observed, "authenticated", "OBS_ACCEPTANCE_AUTHENTICATION_REQUIRED")
    _required_bool(
        observed,
        "loaded_native_artifact_verified",
        "OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH",
    )
    _required_bool(
        observed,
        "installed_python_wheel_verified",
        "OBS_ACCEPTANCE_PYTHON_ARTIFACT_MISMATCH",
    )
    _required_bool(observed, "exact_host_process_bound", "OBS_ACCEPTANCE_HOST_BINDING_FAILED")
    _required_bool(
        observed, "exact_plugin_instance_bound", "OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED"
    )
    _required_bool(observed, "exact_adapter_session_bound", "OBS_ACCEPTANCE_SESSION_BINDING_FAILED")
    _required_bool(observed, "scene_created", "OBS_ACCEPTANCE_SCENE_READBACK_FAILED")
    _required_bool(observed, "scene_readback_verified", "OBS_ACCEPTANCE_SCENE_READBACK_FAILED")
    _required_bool(observed, "source_created", "OBS_ACCEPTANCE_SOURCE_READBACK_FAILED")
    _required_bool(observed, "source_readback_verified", "OBS_ACCEPTANCE_SOURCE_READBACK_FAILED")
    _required_bool(observed, "scene_item_crud_verified", "OBS_ACCEPTANCE_SCENE_GRAPH_FAILED")
    _required_bool(observed, "transition_readback_verified", "OBS_ACCEPTANCE_TRANSITION_FAILED")
    _required_bool(
        observed,
        "studio_mode_preview_program_verified",
        "OBS_ACCEPTANCE_STUDIO_MODE_FAILED",
    )
    recording_digest, recording_size = _validate_recording(observed)

    return {
        "schemaVersion": 1,
        "product": "dcc-mcp-obs",
        "result": "passed",
        "platform": {"name": platform, "architecture": architecture},
        "artifacts": {
            "nativePluginSha256": _required_digest(observed, "native_plugin_sha256"),
            "pythonWheelSha256": _required_digest(observed, "python_wheel_sha256"),
        },
        "versions": versions,
        "binding": {
            "hostProcess": {
                "verified": True,
                "fingerprint": _fingerprint(salt, b"host", host_pid),
            },
            "pluginInstance": {
                "verified": True,
                "fingerprint": _fingerprint(salt, b"plugin", plugin_instance_id),
            },
            "adapterSession": {
                "verified": True,
                "fingerprint": _fingerprint(salt, b"session", adapter_session_id),
            },
        },
        "checks": {
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
        },
        "recording": {"sha256": recording_digest, "sizeBytes": recording_size},
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and publish disposable real-OBS acceptance evidence."
    )
    parser.add_argument("--host-pid", type=int, required=True)
    parser.add_argument("--native-plugin-archive", type=Path, required=True)
    parser.add_argument("--python-wheel", type=Path, required=True)
    parser.add_argument("--disposable-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _verify_disposable_process(host_pid: int, disposable_root: Path):
    import psutil

    try:
        root = disposable_root.resolve(strict=True)
        process = psutil.Process(host_pid)
        executable = Path(process.exe()).resolve(strict=True)
        command = process.cmdline()
        name = process.name().casefold()
    except (OSError, psutil.Error) as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_HOST_BINDING_FAILED") from exc
    if (
        not root.is_dir()
        or name not in OBS_EXECUTABLES
        or "--multi" not in command
        or host_pid <= 0
    ):
        _fail("OBS_ACCEPTANCE_HOST_BINDING_FAILED")
    platform = _platform_name()
    if platform == "windows":
        if "--portable" not in command or not _is_beneath(executable, root):
            _fail("OBS_ACCEPTANCE_NOT_DISPOSABLE")
    else:
        try:
            environment = process.environ()
        except psutil.Error as exc:
            raise AcceptanceContractError("OBS_ACCEPTANCE_NOT_DISPOSABLE") from exc
        config_roots = [
            value for key in ("XDG_CONFIG_HOME", "HOME") if (value := environment.get(key))
        ]
        if not config_roots or not any(_is_beneath(Path(value), root) for value in config_roots):
            _fail("OBS_ACCEPTANCE_NOT_DISPOSABLE")
    return process


def _loaded_native_module_paths(
    process: object,
    *,
    platform: str,
    disposable_root: Path,
    runner: Callable[..., object] = subprocess.run,
) -> list[Path]:
    """Return mapped module paths using a platform-authoritative observer."""

    if platform != "macos":
        try:
            return [Path(value.path) for value in process.memory_maps() if value.path]
        except Exception as exc:
            raise AcceptanceContractError("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH") from exc

    try:
        expected = (
            disposable_root.resolve(strict=True)
            / "home"
            / "Library"
            / "Application Support"
            / "obs-studio"
            / "plugins"
            / "dcc-mcp-obs.plugin"
            / "Contents"
            / "MacOS"
            / "dcc-mcp-obs"
        ).resolve(strict=True)
        pid = process.pid
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
        completed = runner(
            ["/usr/bin/vmmap", "-w", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = getattr(completed, "stdout", None)
        if getattr(completed, "returncode", None) != 0 or not isinstance(output, str):
            _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
        if str(expected) not in output:
            _fail("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH")
        return [expected]
    except AcceptanceContractError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_NATIVE_ARTIFACT_MISMATCH") from exc


def _probe_authentication_required(config: ObsEndpointConfig) -> bool:
    import websocket

    socket = None
    try:
        socket = websocket.create_connection(
            f"ws://{config.host}:{config.port}", timeout=config.timeout_seconds
        )
        raw = socket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        hello = json.loads(raw)
        details = hello.get("d") if isinstance(hello, dict) and hello.get("op") == 0 else None
        authentication = details.get("authentication") if isinstance(details, dict) else None
        return bool(
            isinstance(authentication, dict)
            and isinstance(authentication.get("challenge"), str)
            and authentication["challenge"]
            and isinstance(authentication.get("salt"), str)
            and authentication["salt"]
        )
    except (OSError, UnicodeError, json.JSONDecodeError, websocket.WebSocketException) as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_AUTHENTICATION_REQUIRED") from exc
    finally:
        if socket is not None:
            with suppress(Exception):
                socket.close()


def await_authenticated_status(
    probe: Callable[[], Mapping[str, object]],
    *,
    sleeper: Callable[[float], None] = time.sleep,
    attempts: int = 40,
) -> dict[str, object]:
    """Wait for the authenticated native vendor route to become frontend-ready."""

    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 240:
        _fail("OBS_ACCEPTANCE_READINESS_TIMEOUT")
    for index in range(attempts):
        try:
            status = probe()
            if (
                isinstance(status, Mapping)
                and status.get("ready") is True
                and isinstance(status.get("instanceId"), str)
                and status["instanceId"]
            ):
                return dict(status)
        except Exception:
            pass
        if index + 1 < attempts:
            sleeper(0.25)
    _fail("OBS_ACCEPTANCE_READINESS_TIMEOUT")


def _write_public_evidence(path: Path, evidence: Mapping[str, object]) -> None:
    try:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir() or path.exists():
            _fail("OBS_ACCEPTANCE_OUTPUT_INVALID")
        payload = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except AcceptanceContractError:
        raise
    except OSError as exc:
        raise AcceptanceContractError("OBS_ACCEPTANCE_OUTPUT_INVALID") from exc


def run_real_obs_acceptance(
    *,
    host_pid: int,
    native_plugin_archive: Path,
    python_wheel: Path,
    disposable_root: Path,
    output: Path,
) -> dict[str, object]:
    """Run the live gate against one already-launched disposable OBS process."""

    import platform as platform_module

    from . import server

    process = _verify_disposable_process(host_pid, disposable_root)
    root = disposable_root.resolve(strict=True)
    recordings = root / "recordings"
    if not recordings.is_dir():
        _fail("OBS_ACCEPTANCE_NOT_DISPOSABLE")
    config = ObsEndpointConfig.from_environment()
    if not config.password or not _probe_authentication_required(config):
        _fail("OBS_ACCEPTANCE_AUTHENTICATION_REQUIRED")

    def probe_status() -> Mapping[str, object]:
        transport = ObsWebSocketTransport(config)
        try:
            return ObsControlBridge(transport, expected_pid=host_pid).status()
        finally:
            transport.close()

    status = await_authenticated_status(probe_status)
    expected_instance_id = status.get("instanceId")
    if not isinstance(expected_instance_id, str) or not expected_instance_id:
        _fail("OBS_ACCEPTANCE_INSTANCE_BINDING_FAILED")

    current_platform = _platform_name()
    mapped_files = _loaded_native_module_paths(
        process, platform=current_platform, disposable_root=root
    )
    native = verify_loaded_native_plugin(native_plugin_archive, mapped_files=mapped_files)
    wheel = verify_installed_python_wheel(
        python_wheel, package_root=Path(__file__).resolve().parent
    )
    if (
        native["platform"] != current_platform
        or native["version"] != __version__
        or wheel["version"] != __version__
    ):
        _fail("OBS_ACCEPTANCE_VERSION_INVALID")

    os.environ["DCC_MCP_REGISTRY_DIR"] = str(root / "registry")
    os.environ["DCC_MCP_GATEWAY_PORT"] = "0"
    os.environ["DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS"] = "1"
    instance = server.ObsMcpServer(port=0, host_pid=host_pid)
    instance.register_builtin_actions()
    handle = instance.start()
    client = McpAcceptanceClient(
        handle.mcp_url(), session_id=expected_instance_id, timeout_seconds=30
    )
    instance_stopped = False
    try:
        client.initialize()
        observed = exercise_live_obs(
            client,
            host_pid=host_pid,
            platform=current_platform,
            architecture=platform_module.machine().casefold(),
            native_plugin_sha256=native["archive_sha256"],
            python_wheel_sha256=wheel["wheel_sha256"],
            output_root=recordings,
            authenticated=True,
            loaded_native_artifact_verified=True,
            installed_python_wheel_verified=True,
        )
        evidence = build_public_evidence(observed, salt=os.urandom(32))
        shutdown = client.call("obs_control__request_graceful_shutdown", {})
        shutdown_context = shutdown.get("context")
        if (
            shutdown.get("success") is not True
            or not isinstance(shutdown_context, Mapping)
            or shutdown_context.get("instanceId") != expected_instance_id
            or shutdown_context.get("hostPid") != host_pid
            or shutdown_context.get("shutdownScheduled") is not True
        ):
            _fail("OBS_ACCEPTANCE_GRACEFUL_SHUTDOWN_FAILED")
        # Close the adapter's authenticated OBS WebSocket before waiting for
        # OBS module unload. Otherwise OBS waits for this client while this
        # process waits for OBS, creating a shutdown-order deadlock.
        instance.stop()
        instance_stopped = True
        try:
            process.wait(timeout=15)
        except Exception as exc:
            raise AcceptanceContractError("OBS_ACCEPTANCE_GRACEFUL_SHUTDOWN_FAILED") from exc
        _write_public_evidence(output, evidence)
        return evidence
    finally:
        if not instance_stopped:
            instance.stop()


def main(argv: Sequence[str] | None = None) -> None:
    """Run the real-host gate and print only a stable public result."""

    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        evidence = run_real_obs_acceptance(
            host_pid=args.host_pid,
            native_plugin_archive=args.native_plugin_archive,
            python_wheel=args.python_wheel,
            disposable_root=args.disposable_root,
            output=args.output,
        )
    except AcceptanceContractError as exc:
        print(json.dumps({"result": "failed", "error": str(exc)}, separators=(",", ":")))
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "platform": evidence["platform"],
                "evidence": args.output.name,
            },
            separators=(",", ":"),
        )
    )


__all__ = [
    "AcceptanceContractError",
    "McpAcceptanceClient",
    "build_public_evidence",
    "exercise_live_obs",
    "main",
    "run_real_obs_acceptance",
    "verify_installed_python_wheel",
    "verify_loaded_native_plugin",
]
