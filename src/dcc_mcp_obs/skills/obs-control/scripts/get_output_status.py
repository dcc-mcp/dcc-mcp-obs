from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(output_name=None, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "OBS output status was read.", **bridge.output_status(output_name=output_name)
        )
