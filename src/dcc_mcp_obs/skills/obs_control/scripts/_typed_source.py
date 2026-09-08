from __future__ import annotations

from dcc_mcp_core.skill import skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge, obs_skill_entry

__all__ = ["obs_skill_entry", "typed_source_success"]


def typed_source_success(method_name: str, message: str, **arguments: object):
    with obs_bridge() as bridge:
        method = getattr(bridge, method_name)
        return skill_success(message, **method(**arguments))
