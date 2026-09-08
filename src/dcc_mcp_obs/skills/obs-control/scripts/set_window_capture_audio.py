from dcc_mcp_core.skill import skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge, obs_skill_entry


@obs_skill_entry
def main(
    scene_name,
    source_name,
    process_id,
    window_handle,
    window_title,
    capture_audio,
    capture_method,
    capture_cursor=True,
    client_area=True,
    enabled=True,
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Window capture audio updated and exact binding verified.",
            **bridge.set_window_capture_audio(
                scene_name=scene_name,
                source_name=source_name,
                process_id=process_id,
                window_handle=window_handle,
                window_title=window_title,
                capture_audio=capture_audio,
                capture_method=capture_method,
                capture_cursor=capture_cursor,
                client_area=client_area,
                enabled=enabled,
            ),
        )
