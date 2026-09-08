#pragma once

#include <obs.h>

#include <string>

namespace dcc_mcp_obs {

inline constexpr const char *kPresentationOverlaySourceId = "dcc_mcp_presentation_overlay";
inline constexpr const char *kDefaultPresentationOverlaySourceName = "DCC-MCP Presentation";

struct PresentationOverlayLayout {
	std::string anchor = "top_right";
	int opacity = 88;
	int margin = 48;
	int width = 520;
};

struct PresentationOverlayContent {
	std::string title;
	std::string rows_json = "[]";
	std::string logo_path;
	bool visible = true;
};

bool validate_presentation_overlay_content(const PresentationOverlayContent &content);
void register_presentation_overlay_source();
obs_data_t *create_presentation_overlay(const std::string &scene_name, const std::string &source_name,
					const PresentationOverlayLayout &layout);
obs_data_t *get_presentation_overlay(const std::string &scene_name, const std::string &source_name);
obs_data_t *set_presentation_overlay_layout(const std::string &scene_name, const std::string &source_name,
					    const PresentationOverlayLayout &layout);
obs_data_t *update_presentation_overlay(const std::string &scene_name, const std::string &source_name,
					const PresentationOverlayContent &content);

} // namespace dcc_mcp_obs
