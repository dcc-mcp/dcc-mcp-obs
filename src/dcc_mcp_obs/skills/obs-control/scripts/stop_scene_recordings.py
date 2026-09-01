from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(session_id, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Independent scene recordings stopped and verified.",
            **bridge.stop_scene_recordings(session_id=session_id),
        )
