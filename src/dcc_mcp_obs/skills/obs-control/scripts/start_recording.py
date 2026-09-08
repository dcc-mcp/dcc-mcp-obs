from dcc_mcp_core.skill import skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge, obs_skill_entry


@obs_skill_entry
def main(output_directory=None, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "OBS recording started and was verified.",
            **bridge.start_recording(output_directory=output_directory),
        )
