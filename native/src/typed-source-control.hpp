#pragma once

#include <cstdint>
#include <string>

#include <obs.h>

namespace dcc_mcp_obs {

struct ReviewedSettings {
	bool has_width = false;
	bool has_height = false;
	bool has_color = false;
	bool has_gain_db = false;
	int64_t width = 0;
	int64_t height = 0;
	int64_t color = 0;
	double gain_db = 0.0;
};

struct TypedSourceRequest {
	std::string request_name;
	std::string scene_name;
	std::string source_name;
	std::string new_source_name;
	std::string source_kind;
	std::string schema_version;
	std::string property_name;
	std::string filter_name;
	std::string filter_kind;
	std::string monitor_type;
	ReviewedSettings settings;
	bool enabled = true;
	bool muted = false;
	double volume = 1.0;
	int64_t property_value = 0;
	int64_t media_cursor_ms = 0;
};

bool is_typed_source_request(const std::string &request_name);
bool is_typed_source_mutation(const std::string &request_name);
bool parse_typed_source_request(const std::string &request_name, obs_data_t *request_data, TypedSourceRequest &request,
				const char *&error_code);
obs_data_t *execute_typed_source_request(const TypedSourceRequest &request);

} // namespace dcc_mcp_obs
