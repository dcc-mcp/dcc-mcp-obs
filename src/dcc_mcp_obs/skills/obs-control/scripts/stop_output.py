from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(output_name, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "OBS output stopped and was verified.", **bridge.stop_output(output_name=output_name)
        )
