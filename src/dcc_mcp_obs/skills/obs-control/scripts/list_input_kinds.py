from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(**_kwargs):
    return typed_source_success("list_input_kinds", "Reviewed input kinds listed.")
