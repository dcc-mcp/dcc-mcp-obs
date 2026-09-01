from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(source_name, media_cursor_ms, **_kwargs):
    return typed_source_success(
        "seek_media",
        "Media cursor updated and verified.",
        source_name=source_name,
        media_cursor_ms=media_cursor_ms,
    )
