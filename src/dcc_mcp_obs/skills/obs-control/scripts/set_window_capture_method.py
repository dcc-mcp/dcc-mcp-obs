from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    source_name,
    process_id,
    window_handle,
    window_title,
    capture_method,
    capture_cursor=True,
    client_area=True,
    enabled=True,
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Window capture method updated and exact binding verified.",
            **bridge.set_window_capture_method(
                scene_name=scene_name,
                source_name=source_name,
                process_id=process_id,
                window_handle=window_handle,
                window_title=window_title,
                capture_cursor=capture_cursor,
                client_area=client_area,
                capture_method=capture_method,
                enabled=enabled,
            ),
        )
