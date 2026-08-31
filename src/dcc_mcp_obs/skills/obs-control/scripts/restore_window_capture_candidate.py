from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(executable, process_id, window_handle, window_title, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Exact window-capture candidate restored and capture readiness verified.",
            **bridge.restore_window_capture_candidate(
                executable=executable,
                process_id=process_id,
                window_handle=window_handle,
                window_title=window_title,
            ),
        )
