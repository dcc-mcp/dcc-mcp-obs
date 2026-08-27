"""Exact OBS process selection."""

from __future__ import annotations

import os

import psutil

OBS_EXECUTABLES = {"obs", "obs64", "obs.exe", "obs64.exe"}


class ProcessIdentityError(RuntimeError):
    pass


def resolve_obs_pid(explicit_pid: int | None = None) -> int:
    candidate = explicit_pid
    if candidate is None:
        raw = os.environ.get("DCC_MCP_OBS_HOST_PID", "")
        if raw:
            try:
                candidate = int(raw)
            except ValueError as exc:
                raise ProcessIdentityError("OBS_IDENTITY_INVALID") from exc
    if candidate is not None:
        if isinstance(candidate, bool) or candidate <= 0:
            raise ProcessIdentityError("OBS_IDENTITY_INVALID")
        _require_obs_process(candidate)
        return candidate

    candidates = sorted(
        process.info["pid"]
        for process in psutil.process_iter(["pid", "name"])
        if str(process.info.get("name") or "").casefold() in OBS_EXECUTABLES
    )
    if not candidates:
        raise ProcessIdentityError("OBS_PROCESS_NOT_FOUND")
    if len(candidates) != 1:
        raise ProcessIdentityError("OBS_PROCESS_AMBIGUOUS")
    return candidates[0]


def _require_obs_process(pid: int) -> None:
    try:
        process = psutil.Process(pid)
        name = process.name().casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise ProcessIdentityError("OBS_PROCESS_NOT_FOUND") from exc
    if name not in OBS_EXECUTABLES:
        raise ProcessIdentityError("OBS_PROCESS_MISMATCH")


def process_is_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


__all__ = ["ProcessIdentityError", "process_is_alive", "resolve_obs_pid"]
