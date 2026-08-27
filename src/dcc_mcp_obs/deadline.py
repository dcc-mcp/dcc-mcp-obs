"""Per-call absolute deadline propagated from Core into the OBS bridge."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT_DEADLINE: ContextVar[float | None] = ContextVar(
    "dcc_mcp_obs_deadline",
    default=None,
)


def current_deadline() -> float | None:
    return _CURRENT_DEADLINE.get()


@contextmanager
def deadline_scope(deadline: float | None) -> Iterator[None]:
    token = _CURRENT_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _CURRENT_DEADLINE.reset(token)


__all__ = ["current_deadline", "deadline_scope"]
