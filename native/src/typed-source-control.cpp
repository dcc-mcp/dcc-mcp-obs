#include "typed-source-control.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <set>
#include <tuple>

namespace dcc_mcp_obs {
namespace {

constexpr char kSchemaVersion[] = "1.0";
constexpr char kColorSourceKind[] = "color_source_v3";
constexpr char kGainFilterKind[] = "gain_filter";
constexpr size_t kMaxFilters = 64;

constexpr std::array<const char *, 28> kRequests = {
	"GetSourceIdentity",
	"CreateSource",
	"RenameSource",
	"RemoveSource",
	"ListInputKinds",
	"GetInputSettings",
	"SetInputSettings",
	"DescribeProperties",
	"ValidatePropertyValue",
	"SetPropertyValue",
	"ListFilters",
	"GetFilter",
	"CreateFilter",
	"SetFilterEnabled",
	"SetFilterSettings",
	"RemoveFilter",
	"GetSourceVolume",
	"SetSourceVolume",
	"GetSourceMute",
	"SetSourceMute",
	"GetSourceMonitorType",
	"SetSourceMonitorType",
	"GetMediaStatus",
	"PlayMedia",
	"PauseMedia",
	"RestartMedia",
	"StopMedia",
	"SeekMedia",
};

constexpr std::array<const char *, 17> kMutations = {
	"CreateSource",     "RenameSource",    "RemoveSource",     "SetInputSettings",
	"SetPropertyValue", "CreateFilter",    "SetFilterEnabled", "SetFilterSettings",
	"RemoveFilter",     "SetSourceVolume", "SetSourceMute",    "SetSourceMonitorType",
	"PlayMedia",        "PauseMedia",      "RestartMedia",     "StopMedia",
	"SeekMedia",
};

template<size_t N> bool one_of(const std::string &value, const std::array<const char *, N> &values)
{
	return std::find_if(values.begin(), values.end(), [&](const char *item) { return value == item; }) !=
	       values.end();
}

bool valid_name(const char *value)
{
	return value != nullptr && *value != '\0' && std::strlen(value) <= 256;
}

void set_error(obs_data_t *result, const char *code)
{
	obs_data_set_bool(result, "ok", false);
	obs_data_set_string(result, "errorCode", code);
}

bool require_capability(obs_data_t *data, const char *expected)
{
	const char *actual = data != nullptr ? obs_data_get_string(data, "capability") : nullptr;
	return actual != nullptr && std::strcmp(actual, expected) == 0;
}

bool require_schema_kind(obs_data_t *data, const char *kind_field, const char *expected_kind, std::string &kind,
			 std::string &version, const char *&error_code)
{
	const char *raw_kind = data != nullptr ? obs_data_get_string(data, kind_field) : nullptr;
	const char *raw_version = data != nullptr ? obs_data_get_string(data, "schemaVersion") : nullptr;
	kind = raw_kind != nullptr ? raw_kind : "";
	version = raw_version != nullptr ? raw_version : "";
	if (version != kSchemaVersion) {
		error_code = "OBS_SCHEMA_UNSUPPORTED";
		return false;
	}
	if (kind != expected_kind) {
		error_code = std::strcmp(kind_field, "filterKind") == 0 ? "OBS_FILTER_KIND_UNSUPPORTED"
									: "OBS_SOURCE_KIND_UNSUPPORTED";
		return false;
	}
	return true;
}

bool parse_color_settings(obs_data_t *data, ReviewedSettings &settings, const char *&error_code)
{
	if (data == nullptr) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	bool any = false;
	obs_data_item_t *item = obs_data_first(data);
	while (item != nullptr) {
		const char *name = obs_data_item_get_name(item);
		if (name == nullptr || obs_data_item_gettype(item) != OBS_DATA_NUMBER ||
		    obs_data_item_numtype(item) != OBS_DATA_NUM_INT) {
			error_code = "OBS_ARGUMENT_INVALID";
			obs_data_item_release(&item);
			return false;
		}
		const long long value = obs_data_get_int(data, name);
		if (std::strcmp(name, "width") == 0 && value >= 1 && value <= 8192) {
			settings.has_width = true;
			settings.width = value;
		} else if (std::strcmp(name, "height") == 0 && value >= 1 && value <= 8192) {
			settings.has_height = true;
			settings.height = value;
		} else if (std::strcmp(name, "color") == 0 && value >= 0 &&
			   static_cast<uint64_t>(value) <= std::numeric_limits<uint32_t>::max()) {
			settings.has_color = true;
			settings.color = value;
		} else {
			error_code = "OBS_ARGUMENT_INVALID";
			obs_data_item_release(&item);
			return false;
		}
		any = true;
		obs_data_item_next(&item);
	}
	if (!any)
		error_code = "OBS_ARGUMENT_INVALID";
	return any;
}

bool parse_gain_settings(obs_data_t *data, ReviewedSettings &settings, const char *&error_code)
{
	if (data == nullptr) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	obs_data_item_t *item = obs_data_first(data);
	if (item == nullptr || std::strcmp(obs_data_item_get_name(item), "db") != 0 ||
	    obs_data_item_gettype(item) != OBS_DATA_NUMBER) {
		if (item != nullptr)
			obs_data_item_release(&item);
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	const double value = obs_data_get_double(data, "db");
	if (!std::isfinite(value) || value < -30.0 || value > 30.0) {
		obs_data_item_release(&item);
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	settings.has_gain_db = true;
	settings.gain_db = value;
	if (obs_data_item_next(&item)) {
		obs_data_item_release(&item);
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	return true;
}

obs_data_t *settings_data(const ReviewedSettings &settings)
{
	auto *data = obs_data_create();
	if (settings.has_width)
		obs_data_set_int(data, "width", settings.width);
	if (settings.has_height)
		obs_data_set_int(data, "height", settings.height);
	if (settings.has_color)
		obs_data_set_int(data, "color", settings.color);
	if (settings.has_gain_db)
		obs_data_set_double(data, "db", settings.gain_db);
	return data;
}

const char *source_type_name(obs_source_t *source)
{
	switch (obs_source_get_type(source)) {
	case OBS_SOURCE_TYPE_INPUT:
		return "input";
	case OBS_SOURCE_TYPE_FILTER:
		return "filter";
	case OBS_SOURCE_TYPE_SCENE:
		return "scene";
	case OBS_SOURCE_TYPE_TRANSITION:
		return "transition";
	default:
		return "unknown";
	}
}

obs_data_t *source_identity(const std::string &source_name)
{
	auto *result = obs_data_create();
	auto *source = obs_get_source_by_name(source_name.c_str());
	if (source == nullptr) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	obs_data_set_string(result, "sourceName", obs_source_get_name(source));
	obs_data_set_string(result, "sourceKind", obs_source_get_id(source));
	obs_data_set_string(result, "sourceType", source_type_name(source));
	obs_data_set_int(result, "outputFlags", obs_source_get_output_flags(source));
	obs_data_set_bool(result, "active", obs_source_active(source));
	obs_data_set_bool(result, "showing", obs_source_showing(source));
	obs_data_set_int(result, "width", obs_source_get_width(source));
	obs_data_set_int(result, "height", obs_source_get_height(source));
	obs_source_release(source);
	return result;
}

bool reviewed_input(obs_source_t *source)
{
	return source != nullptr && obs_source_get_type(source) == OBS_SOURCE_TYPE_INPUT &&
	       std::strcmp(obs_source_get_id(source), kColorSourceKind) == 0;
}

obs_data_t *input_settings(const TypedSourceRequest &request)
{
	auto *result = obs_data_create();
	auto *source = obs_get_source_by_name(request.source_name.c_str());
	if (source == nullptr)
		set_error(result, "OBS_SOURCE_NOT_FOUND");
	else if (!reviewed_input(source))
		set_error(result, "OBS_SOURCE_KIND_UNSUPPORTED");
	else {
		auto *raw = obs_source_get_settings(source);
		auto *settings = obs_data_create();
		obs_data_set_int(settings, "width", obs_data_get_int(raw, "width"));
		obs_data_set_int(settings, "height", obs_data_get_int(raw, "height"));
		obs_data_set_int(settings, "color", obs_data_get_int(raw, "color"));
		obs_data_set_string(result, "sourceName", obs_source_get_name(source));
		obs_data_set_string(result, "sourceKind", kColorSourceKind);
		obs_data_set_string(result, "schemaVersion", kSchemaVersion);
		obs_data_set_obj(result, "settings", settings);
		obs_data_release(settings);
		obs_data_release(raw);
	}
	if (source != nullptr)
		obs_source_release(source);
	return result;
}

obs_data_t *filter_status(const TypedSourceRequest &request)
{
	auto *result = obs_data_create();
	auto *source = obs_get_source_by_name(request.source_name.c_str());
	auto *filter = source != nullptr ? obs_source_get_filter_by_name(source, request.filter_name.c_str()) : nullptr;
	if (source == nullptr)
		set_error(result, "OBS_SOURCE_NOT_FOUND");
	else if (filter == nullptr)
		set_error(result, "OBS_SOURCE_NOT_FOUND");
	else if (std::strcmp(obs_source_get_id(filter), kGainFilterKind) != 0)
		set_error(result, "OBS_FILTER_KIND_UNSUPPORTED");
	else {
		auto *raw = obs_source_get_settings(filter);
		auto *settings = obs_data_create();
		obs_data_set_double(settings, "db", obs_data_get_double(raw, "db"));
		obs_data_set_string(result, "sourceName", request.source_name.c_str());
		obs_data_set_string(result, "filterName", obs_source_get_name(filter));
		obs_data_set_string(result, "filterKind", kGainFilterKind);
		obs_data_set_bool(result, "enabled", obs_source_enabled(filter));
		obs_data_set_string(result, "schemaVersion", kSchemaVersion);
		obs_data_set_obj(result, "settings", settings);
		obs_data_release(settings);
		obs_data_release(raw);
	}
	if (filter != nullptr)
		obs_source_release(filter);
	if (source != nullptr)
		obs_source_release(source);
	return result;
}

struct FilterListContext {
	obs_data_array_t *filters;
	size_t count = 0;
	bool truncated = false;
};

void append_filter(obs_source_t *, obs_source_t *filter, void *param)
{
	auto *context = static_cast<FilterListContext *>(param);
	if (context->count >= kMaxFilters) {
		context->truncated = true;
		return;
	}
	auto *item = obs_data_create();
	obs_data_set_string(item, "filterName", obs_source_get_name(filter));
	obs_data_set_string(item, "filterKind", obs_source_get_id(filter));
	obs_data_set_bool(item, "enabled", obs_source_enabled(filter));
	obs_data_array_push_back(context->filters, item);
	obs_data_release(item);
	++context->count;
}

const char *monitor_type_name(enum obs_monitoring_type type)
{
	switch (type) {
	case OBS_MONITORING_TYPE_MONITOR_ONLY:
		return "monitor_only";
	case OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT:
		return "monitor_and_output";
	default:
		return "none";
	}
}

enum obs_monitoring_type monitor_type_value(const std::string &type)
{
	if (type == "monitor_only")
		return OBS_MONITORING_TYPE_MONITOR_ONLY;
	if (type == "monitor_and_output")
		return OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT;
	return OBS_MONITORING_TYPE_NONE;
}

const char *media_state_name(enum obs_media_state state)
{
	switch (state) {
	case OBS_MEDIA_STATE_PLAYING:
		return "playing";
	case OBS_MEDIA_STATE_OPENING:
		return "opening";
	case OBS_MEDIA_STATE_BUFFERING:
		return "buffering";
	case OBS_MEDIA_STATE_PAUSED:
		return "paused";
	case OBS_MEDIA_STATE_STOPPED:
		return "stopped";
	case OBS_MEDIA_STATE_ENDED:
		return "ended";
	case OBS_MEDIA_STATE_ERROR:
		return "error";
	default:
		return "none";
	}
}

obs_data_t *media_status(const std::string &source_name)
{
	auto *result = obs_data_create();
	auto *source = obs_get_source_by_name(source_name.c_str());
	if (source == nullptr)
		set_error(result, "OBS_SOURCE_NOT_FOUND");
	else if ((obs_source_get_output_flags(source) & OBS_SOURCE_CONTROLLABLE_MEDIA) == 0)
		set_error(result, "OBS_MEDIA_NOT_CONTROLLABLE");
	else {
		obs_data_set_string(result, "sourceName", source_name.c_str());
		obs_data_set_string(result, "mediaState", media_state_name(obs_source_media_get_state(source)));
		obs_data_set_int(result, "mediaDurationMs",
				 std::max<int64_t>(0, obs_source_media_get_duration(source)));
		obs_data_set_int(result, "mediaCursorMs", std::max<int64_t>(0, obs_source_media_get_time(source)));
	}
	if (source != nullptr)
		obs_source_release(source);
	return result;
}

} // namespace

bool is_typed_source_request(const std::string &request_name)
{
	return one_of(request_name, kRequests);
}

bool is_typed_source_mutation(const std::string &request_name)
{
	return one_of(request_name, kMutations);
}

bool parse_typed_source_request(const std::string &request_name, obs_data_t *data, TypedSourceRequest &request,
				const char *&error_code)
{
	request.request_name = request_name;
	if (!is_typed_source_request(request_name)) {
		error_code = "OBS_REQUEST_INVALID";
		return false;
	}
	const char *source_name = data != nullptr ? obs_data_get_string(data, "sourceName") : nullptr;
	const bool needs_source = request_name != "ListInputKinds" && request_name != "DescribeProperties" &&
				  request_name != "ValidatePropertyValue";
	if (needs_source && !valid_name(source_name)) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	if (source_name != nullptr)
		request.source_name = source_name;

	const bool input_request = request_name == "CreateSource" || request_name == "GetInputSettings" ||
				   request_name == "SetInputSettings" || request_name == "DescribeProperties" ||
				   request_name == "ValidatePropertyValue" || request_name == "SetPropertyValue";
	if (input_request && !require_schema_kind(data, "sourceKind", kColorSourceKind, request.source_kind,
						  request.schema_version, error_code))
		return false;
	const bool filter_request = request_name == "CreateFilter" || request_name == "SetFilterSettings";
	if (filter_request && !require_schema_kind(data, "filterKind", kGainFilterKind, request.filter_kind,
						   request.schema_version, error_code))
		return false;

	if (request_name == "CreateSource") {
		const char *scene_name = obs_data_get_string(data, "sceneName");
		if (!valid_name(scene_name) || !obs_data_has_user_value(data, "enabled") ||
		    !require_capability(data, "sources")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		request.scene_name = scene_name;
		request.enabled = obs_data_get_bool(data, "enabled");
		auto *settings = obs_data_get_obj(data, "settings");
		const bool valid = parse_color_settings(settings, request.settings, error_code);
		if (settings != nullptr)
			obs_data_release(settings);
		return valid;
	}
	if (request_name == "RenameSource") {
		const char *value = obs_data_get_string(data, "newSourceName");
		if (!valid_name(value) || request.source_name == value || !require_capability(data, "sources")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		request.new_source_name = value;
	}
	if (request_name == "RemoveSource" && !require_capability(data, "sources")) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	if (request_name == "SetInputSettings") {
		if (!require_capability(data, "inputs")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		auto *settings = obs_data_get_obj(data, "settings");
		const bool valid = parse_color_settings(settings, request.settings, error_code);
		if (settings != nullptr)
			obs_data_release(settings);
		if (!valid)
			return false;
	}
	if (request_name == "ValidatePropertyValue" || request_name == "SetPropertyValue") {
		const char *property_name = obs_data_get_string(data, "propertyName");
		if (property_name == nullptr ||
		    (std::strcmp(property_name, "width") != 0 && std::strcmp(property_name, "height") != 0 &&
		     std::strcmp(property_name, "color") != 0) ||
		    !obs_data_has_user_value(data, "value") ||
		    (request_name == "SetPropertyValue" && !require_capability(data, "properties"))) {
			error_code = "OBS_PROPERTY_NOT_FOUND";
			return false;
		}
		request.property_name = property_name;
		request.property_value = obs_data_get_int(data, "value");
		auto *settings = obs_data_create();
		obs_data_set_int(settings, property_name, request.property_value);
		const bool valid = parse_color_settings(settings, request.settings, error_code);
		obs_data_release(settings);
		if (!valid)
			return false;
	}

	const bool named_filter = request_name == "GetFilter" || request_name == "CreateFilter" ||
				  request_name == "SetFilterEnabled" || request_name == "SetFilterSettings" ||
				  request_name == "RemoveFilter";
	if (named_filter) {
		const char *filter_name = obs_data_get_string(data, "filterName");
		if (!valid_name(filter_name)) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		request.filter_name = filter_name;
	}
	if (request_name == "CreateFilter" || request_name == "SetFilterSettings") {
		if (!require_capability(data, "filters")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		auto *settings = obs_data_get_obj(data, "settings");
		const bool valid = parse_gain_settings(settings, request.settings, error_code);
		if (settings != nullptr)
			obs_data_release(settings);
		if (!valid)
			return false;
	}
	if (request_name == "CreateFilter" || request_name == "SetFilterEnabled") {
		if (!obs_data_has_user_value(data, "enabled") || !require_capability(data, "filters")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		request.enabled = obs_data_get_bool(data, "enabled");
	}
	if (request_name == "RemoveFilter" && !require_capability(data, "filters")) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}

	if (request_name == "SetSourceVolume") {
		request.volume = obs_data_get_double(data, "volume");
		if (!obs_data_has_user_value(data, "volume") || !std::isfinite(request.volume) ||
		    request.volume < 0.0 || request.volume > 20.0 || !require_capability(data, "audio")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
	}
	if (request_name == "SetSourceMute") {
		if (!obs_data_has_user_value(data, "muted") || !require_capability(data, "audio")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
		request.muted = obs_data_get_bool(data, "muted");
	}
	if (request_name == "SetSourceMonitorType") {
		const char *value = obs_data_get_string(data, "monitorType");
		request.monitor_type = value != nullptr ? value : "";
		if ((request.monitor_type != "none" && request.monitor_type != "monitor_only" &&
		     request.monitor_type != "monitor_and_output") ||
		    !require_capability(data, "audio")) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
	}
	if ((request_name == "PlayMedia" || request_name == "PauseMedia" || request_name == "RestartMedia" ||
	     request_name == "StopMedia" || request_name == "SeekMedia") &&
	    !require_capability(data, "media")) {
		error_code = "OBS_ARGUMENT_INVALID";
		return false;
	}
	if (request_name == "SeekMedia") {
		request.media_cursor_ms = obs_data_get_int(data, "mediaCursorMs");
		if (!obs_data_has_user_value(data, "mediaCursorMs") || request.media_cursor_ms < 0 ||
		    request.media_cursor_ms > 86400000) {
			error_code = "OBS_ARGUMENT_INVALID";
			return false;
		}
	}
	return true;
}

obs_data_t *execute_typed_source_request(const TypedSourceRequest &request)
{
	if (request.request_name == "GetSourceIdentity")
		return source_identity(request.source_name);
	if (request.request_name == "ListInputKinds") {
		auto *result = obs_data_create();
		auto *items = obs_data_array_create();
		auto *item = obs_data_create();
		const char *display = obs_source_get_display_name(kColorSourceKind);
		obs_data_set_string(item, "sourceKind", kColorSourceKind);
		obs_data_set_string(item, "displayName", display != nullptr ? display : "Color Source");
		obs_data_array_push_back(items, item);
		obs_data_set_string(result, "schemaVersion", kSchemaVersion);
		obs_data_set_array(result, "inputKinds", items);
		obs_data_release(item);
		obs_data_array_release(items);
		return result;
	}
	if (request.request_name == "GetInputSettings")
		return input_settings(request);
	if (request.request_name == "DescribeProperties") {
		auto *result = obs_data_create();
		auto *items = obs_data_array_create();
		for (const auto &[name, minimum, maximum] : std::array<std::tuple<const char *, int64_t, int64_t>, 3>{{
			     {"width", 1, 8192},
			     {"height", 1, 8192},
			     {"color", 0, std::numeric_limits<uint32_t>::max()},
		     }}) {
			auto *item = obs_data_create();
			obs_data_set_string(item, "propertyName", name);
			obs_data_set_string(item, "valueType", "integer");
			obs_data_set_int(item, "minimum", minimum);
			obs_data_set_int(item, "maximum", maximum);
			obs_data_array_push_back(items, item);
			obs_data_release(item);
		}
		obs_data_set_string(result, "sourceKind", kColorSourceKind);
		obs_data_set_string(result, "schemaVersion", kSchemaVersion);
		obs_data_set_array(result, "properties", items);
		obs_data_array_release(items);
		return result;
	}
	if (request.request_name == "ValidatePropertyValue") {
		auto *result = obs_data_create();
		obs_data_set_string(result, "sourceKind", kColorSourceKind);
		obs_data_set_string(result, "schemaVersion", kSchemaVersion);
		obs_data_set_string(result, "propertyName", request.property_name.c_str());
		obs_data_set_int(result, "value", request.property_value);
		obs_data_set_bool(result, "valid", true);
		return result;
	}
	if (request.request_name == "ListFilters") {
		auto *result = obs_data_create();
		auto *source = obs_get_source_by_name(request.source_name.c_str());
		if (source == nullptr) {
			set_error(result, "OBS_SOURCE_NOT_FOUND");
			return result;
		}
		auto *items = obs_data_array_create();
		FilterListContext context{items};
		obs_source_enum_filters(source, append_filter, &context);
		obs_data_set_string(result, "sourceName", request.source_name.c_str());
		obs_data_set_array(result, "filters", items);
		obs_data_set_bool(result, "truncated", context.truncated);
		obs_data_array_release(items);
		obs_source_release(source);
		return result;
	}
	if (request.request_name == "GetFilter")
		return filter_status(request);

	if (request.request_name == "GetSourceVolume" || request.request_name == "GetSourceMute" ||
	    request.request_name == "GetSourceMonitorType") {
		auto *result = obs_data_create();
		auto *source = obs_get_source_by_name(request.source_name.c_str());
		if (source == nullptr)
			set_error(result, "OBS_SOURCE_NOT_FOUND");
		else {
			obs_data_set_string(result, "sourceName", request.source_name.c_str());
			if (request.request_name == "GetSourceVolume")
				obs_data_set_double(result, "volume", obs_source_get_volume(source));
			else if (request.request_name == "GetSourceMute")
				obs_data_set_bool(result, "muted", obs_source_muted(source));
			else
				obs_data_set_string(result, "monitorType",
						    monitor_type_name(obs_source_get_monitoring_type(source)));
		}
		if (source != nullptr)
			obs_source_release(source);
		return result;
	}
	if (request.request_name == "GetMediaStatus")
		return media_status(request.source_name);

	auto *result = obs_data_create();
	if (request.request_name == "CreateSource") {
		auto *scene_source = obs_get_source_by_name(request.scene_name.c_str());
		auto *existing = obs_get_source_by_name(request.source_name.c_str());
		if (scene_source == nullptr || obs_source_get_type(scene_source) != OBS_SOURCE_TYPE_SCENE)
			set_error(result, "OBS_SCENE_NOT_FOUND");
		else if (existing != nullptr)
			set_error(result, "OBS_TARGET_AMBIGUOUS");
		else {
			auto *settings = settings_data(request.settings);
			auto *source =
				obs_source_create(kColorSourceKind, request.source_name.c_str(), settings, nullptr);
			auto *scene = obs_scene_from_source(scene_source);
			auto *item = source != nullptr && scene != nullptr ? obs_scene_add(scene, source) : nullptr;
			obs_data_release(settings);
			if (item == nullptr)
				set_error(result, "OBS_MUTATION_REJECTED");
			else {
				obs_sceneitem_set_visible(item, request.enabled);
				obs_data_set_bool(result, "accepted", true);
			}
			if (source != nullptr)
				obs_source_release(source);
		}
		if (existing != nullptr)
			obs_source_release(existing);
		if (scene_source != nullptr)
			obs_source_release(scene_source);
		return result;
	}

	auto *source = obs_get_source_by_name(request.source_name.c_str());
	if (source == nullptr) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	if (request.request_name == "RenameSource") {
		auto *existing = obs_get_source_by_name(request.new_source_name.c_str());
		if (obs_source_get_type(source) != OBS_SOURCE_TYPE_INPUT)
			set_error(result, "OBS_SOURCE_KIND_UNSUPPORTED");
		else if (existing != nullptr)
			set_error(result, "OBS_TARGET_AMBIGUOUS");
		else {
			obs_source_set_name(source, request.new_source_name.c_str());
			if (request.new_source_name != obs_source_get_name(source))
				set_error(result, "OBS_POSTCONDITION_FAILED");
			else {
				obs_data_set_bool(result, "accepted", true);
				obs_data_set_string(result, "newSourceName", request.new_source_name.c_str());
			}
		}
		if (existing != nullptr)
			obs_source_release(existing);
	} else if (request.request_name == "RemoveSource") {
		if (obs_source_get_type(source) != OBS_SOURCE_TYPE_INPUT)
			set_error(result, "OBS_SOURCE_KIND_UNSUPPORTED");
		else {
			obs_source_remove(source);
			auto *remaining = obs_get_source_by_name(request.source_name.c_str());
			if (remaining != nullptr) {
				obs_source_release(remaining);
				set_error(result, "OBS_POSTCONDITION_FAILED");
			} else {
				obs_data_set_bool(result, "accepted", true);
				obs_data_set_bool(result, "removed", true);
			}
		}
	} else if (request.request_name == "SetInputSettings" || request.request_name == "SetPropertyValue") {
		if (!reviewed_input(source))
			set_error(result, "OBS_SOURCE_KIND_UNSUPPORTED");
		else {
			auto *settings = settings_data(request.settings);
			obs_source_update(source, settings);
			obs_data_release(settings);
			obs_data_set_bool(result, "accepted", true);
		}
	} else if (request.request_name == "CreateFilter") {
		auto *existing = obs_source_get_filter_by_name(source, request.filter_name.c_str());
		if (existing != nullptr)
			set_error(result, "OBS_TARGET_AMBIGUOUS");
		else {
			auto *settings = settings_data(request.settings);
			auto *filter =
				obs_source_create(kGainFilterKind, request.filter_name.c_str(), settings, nullptr);
			obs_data_release(settings);
			if (filter == nullptr)
				set_error(result, "OBS_MUTATION_REJECTED");
			else {
				obs_source_set_enabled(filter, request.enabled);
				obs_source_filter_add(source, filter);
				obs_data_set_bool(result, "accepted", true);
				obs_source_release(filter);
			}
		}
		if (existing != nullptr)
			obs_source_release(existing);
	} else if (request.request_name == "SetFilterEnabled" || request.request_name == "SetFilterSettings" ||
		   request.request_name == "RemoveFilter") {
		auto *filter = obs_source_get_filter_by_name(source, request.filter_name.c_str());
		if (filter == nullptr)
			set_error(result, "OBS_SOURCE_NOT_FOUND");
		else if (request.request_name == "SetFilterEnabled") {
			obs_source_set_enabled(filter, request.enabled);
			obs_data_set_bool(result, "accepted", true);
		} else if (request.request_name == "SetFilterSettings") {
			if (std::strcmp(obs_source_get_id(filter), kGainFilterKind) != 0)
				set_error(result, "OBS_FILTER_KIND_UNSUPPORTED");
			else {
				auto *settings = settings_data(request.settings);
				obs_source_update(filter, settings);
				obs_data_release(settings);
				obs_data_set_bool(result, "accepted", true);
			}
		} else {
			obs_source_filter_remove(source, filter);
			auto *remaining = obs_source_get_filter_by_name(source, request.filter_name.c_str());
			if (remaining != nullptr) {
				obs_source_release(remaining);
				set_error(result, "OBS_POSTCONDITION_FAILED");
			} else {
				obs_data_set_bool(result, "accepted", true);
				obs_data_set_bool(result, "removed", true);
			}
		}
		if (filter != nullptr)
			obs_source_release(filter);
	} else if (request.request_name == "SetSourceVolume") {
		obs_source_set_volume(source, static_cast<float>(request.volume));
		obs_data_set_bool(result, "accepted", true);
	} else if (request.request_name == "SetSourceMute") {
		obs_source_set_muted(source, request.muted);
		obs_data_set_bool(result, "accepted", true);
	} else if (request.request_name == "SetSourceMonitorType") {
		obs_source_set_monitoring_type(source, monitor_type_value(request.monitor_type));
		obs_data_set_bool(result, "accepted", true);
	} else if ((obs_source_get_output_flags(source) & OBS_SOURCE_CONTROLLABLE_MEDIA) == 0) {
		set_error(result, "OBS_MEDIA_NOT_CONTROLLABLE");
	} else {
		if (request.request_name == "PlayMedia")
			obs_source_media_play_pause(source, false);
		else if (request.request_name == "PauseMedia")
			obs_source_media_play_pause(source, true);
		else if (request.request_name == "RestartMedia")
			obs_source_media_restart(source);
		else if (request.request_name == "StopMedia")
			obs_source_media_stop(source);
		else if (request.request_name == "SeekMedia")
			obs_source_media_set_time(source, request.media_cursor_ms);
		obs_data_set_bool(result, "accepted", true);
	}
	obs_source_release(source);
	return result;
}

} // namespace dcc_mcp_obs
