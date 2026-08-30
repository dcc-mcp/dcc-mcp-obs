from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    source_name,
    process_id,
    window_handle,
    window_title,
    capture_cursor=True,
    client_area=True,
    enabled=True,
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Window capture source created and exact binding verified.",
            **bridge.create_window_capture_source(
                scene_name=scene_name,
                source_name=source_name,
                process_id=process_id,
                window_handle=window_handle,
                window_title=window_title,
                capture_cursor=capture_cursor,
                client_area=client_area,
                enabled=enabled,
            ),
        )
