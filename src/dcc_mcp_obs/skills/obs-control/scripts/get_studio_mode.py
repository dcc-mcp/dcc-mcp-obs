from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(**_kwargs):
    with obs_bridge() as bridge:
        return skill_success("Studio Mode status read.", **bridge.get_studio_mode_status())
