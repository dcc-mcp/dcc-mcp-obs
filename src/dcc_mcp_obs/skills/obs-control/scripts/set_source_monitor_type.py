from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(source_name, monitor_type, **_kwargs):
    return typed_source_success(
        "set_source_monitor_type",
        "Source monitor type updated and verified.",
        source_name=source_name,
        monitor_type=monitor_type,
    )
