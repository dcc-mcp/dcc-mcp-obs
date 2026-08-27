"""Dispatcher for the native OBS control bridge."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from .bridge import BridgeError
from .deadline import deadline_scope


class ObsBridgeDispatcher:
    """Run wrappers inline; the native plugin owns OBS UI-thread dispatch."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def dispatch_callable(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        timeout_hint = kwargs.pop("timeout_hint_secs", None)
        for key in (
            "affinity",
            "context",
            "action_name",
            "skill_name",
            "execution",
            "thread_affinity",
        ):
            kwargs.pop(key, None)
        deadline: float | None = None
        if timeout_hint is not None:
            if (
                not isinstance(timeout_hint, (int, float))
                or isinstance(timeout_hint, bool)
                or not math.isfinite(timeout_hint)
                or timeout_hint <= 0
            ):
                raise BridgeError("OBS_ARGUMENT_INVALID")
            deadline = self._clock() + float(timeout_hint)
        with deadline_scope(deadline):
            return func(*args, **kwargs)


__all__ = ["ObsBridgeDispatcher"]
