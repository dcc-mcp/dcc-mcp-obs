from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(profile_name, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "The OBS profile was selected and verified.", **bridge.set_current_profile(profile_name)
        )
