from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(executable, window_title=None, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Exact visible window-capture candidates read.",
            **bridge.list_window_capture_candidates(
                executable=executable,
                window_title=window_title,
            ),
        )
