from dcc_mcp_core.skill import skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge, obs_skill_entry


@obs_skill_entry
def main(**_kwargs):
    with obs_bridge() as bridge:
        result = bridge.stop_recording()
        message = (
            "OBS recording stopped and its output was classified."
            if result["verified"]
            else "OBS accepted the stop request and is still finalizing the output."
        )
        return skill_success(message, **result)
