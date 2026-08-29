from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(transition_name, duration_ms=None, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Transition selected and verified.",
            **bridge.set_current_transition(transition_name, duration_ms=duration_ms),
        )
