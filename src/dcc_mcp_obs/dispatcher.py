"""Dispatcher for the native OBS control bridge."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ObsBridgeDispatcher:
    """Run wrappers inline; the native plugin owns OBS UI-thread dispatch."""

    def dispatch_callable(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        for key in (
            "affinity",
            "context",
            "action_name",
            "skill_name",
            "execution",
            "timeout_hint_secs",
            "thread_affinity",
        ):
            kwargs.pop(key, None)
        return func(*args, **kwargs)


__all__ = ["ObsBridgeDispatcher"]
