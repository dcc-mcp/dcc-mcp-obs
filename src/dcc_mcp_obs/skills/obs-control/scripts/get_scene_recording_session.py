from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(session_id, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Independent scene recording session read.",
            **bridge.scene_recording_status(session_id=session_id),
        )
