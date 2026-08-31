#pragma once

#include <obs.h>

#include <cstdint>
#include <string>
#include <vector>

namespace dcc_mcp_obs {

inline constexpr const char *kAgentInputOverlaySourceId = "dcc_mcp_agent_input_overlay";
inline constexpr const char *kDefaultAgentInputOverlaySourceName = "DCC-MCP Agent Input";

struct AgentInputActivity {
	std::string event_kind;
	std::vector<std::string> keys;
	std::string mouse_button = "none";
	std::string wheel_direction = "none";
	int character_count = 0;
	int duration_ms = 1600;
};

void register_agent_input_overlay_source();

obs_data_t *create_agent_input_overlay(const std::string &scene_name, const std::string &source_name,
				       const std::string &anchor);
obs_data_t *get_agent_input_overlay(const std::string &scene_name, const std::string &source_name);
obs_data_t *emit_agent_input_activity(const std::string &scene_name, const std::string &source_name,
				      const AgentInputActivity &activity);
obs_data_t *clear_agent_input_overlay(const std::string &scene_name, const std::string &source_name);

} // namespace dcc_mcp_obs
