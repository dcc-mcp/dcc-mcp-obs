from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(scene_name, source_name="DCC-MCP Agent Input", **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "OBS Agent input overlay cleared and verified.",
            **bridge.clear_agent_input_overlay(scene_name=scene_name, source_name=source_name),
        )
