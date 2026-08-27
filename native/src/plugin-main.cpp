#include <obs-frontend-api.h>
#include <obs-module.h>
#include <util/platform.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <random>
#include <sstream>
#include <string>

#ifdef _WIN32
#define NOMINMAX
#include <Windows.h>
#else
#include <unistd.h>
#endif

#include "obs-websocket-api.h"
#include "ui-task-gate.hpp"

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("dcc-mcp-obs", "en-US")

namespace {

constexpr char kVendorName[] = "dcc-mcp-obs";
constexpr char kPluginVersion[] = "0.1.0"; // x-release-please-version
constexpr auto kUiTimeout = std::chrono::seconds(5);
constexpr size_t kMaxScenes = 256;
constexpr size_t kMaxSources = 512;

obs_websocket_vendor g_vendor = nullptr;
std::atomic<uint64_t> g_event_sequence{0};
std::string g_instance_id;

enum class UiOperation {
	Status,
	ListScenes,
	ListSources,
	RecordingStatus,
	StartRecording,
	StopRecording,
	PauseRecording,
	ResumeRecording,
};

struct UiState {
	UiOperation operation;
	std::string scene_name;
	std::mutex mutex;
	std::condition_variable condition;
	bool complete = false;
	obs_data_t *result = nullptr;
	dcc_mcp_obs::UiTaskGate gate;

	~UiState()
	{
		if (result != nullptr)
			obs_data_release(result);
	}
};

uint64_t current_pid()
{
#ifdef _WIN32
	return static_cast<uint64_t>(GetCurrentProcessId());
#else
	return static_cast<uint64_t>(getpid());
#endif
}

std::string make_instance_id()
{
	std::random_device device;
	std::mt19937_64 random(device());
	std::ostringstream value;
	value << std::hex << current_pid() << '-' << random() << '-' << random();
	return value.str();
}

void set_identity(obs_data_t *data, uint64_t event_sequence)
{
	obs_data_set_string(data, "instanceId", g_instance_id.c_str());
	obs_data_set_string(data, "pluginVersion", kPluginVersion);
	obs_data_set_string(data, "obsVersion", obs_get_version_string());
	obs_data_set_int(data, "hostPid", static_cast<long long>(current_pid()));
	obs_data_set_int(data, "eventSequence", static_cast<long long>(event_sequence));
}

void set_error(obs_data_t *data, const char *code)
{
	const uint64_t event_sequence = g_event_sequence.fetch_add(1) + 1;
	set_identity(data, event_sequence);
	obs_data_set_bool(data, "ok", false);
	obs_data_set_string(data, "errorCode", code);
}

bool append_scene_item(obs_scene_t *, obs_sceneitem_t *item, void *private_data)
{
	auto *array = static_cast<obs_data_array_t *>(private_data);
	if (obs_data_array_count(array) >= kMaxSources)
		return false;
	obs_source_t *source = obs_sceneitem_get_source(item);
	if (source == nullptr)
		return true;

	obs_data_t *entry = obs_data_create();
	obs_data_set_int(entry, "sceneItemId", obs_sceneitem_get_id(item));
	obs_data_set_string(entry, "sourceName", obs_source_get_name(source));
	obs_data_set_string(entry, "sourceKind", obs_source_get_id(source));
	obs_data_set_bool(entry, "enabled", obs_sceneitem_visible(item));
	obs_data_array_push_back(array, entry);
	obs_data_release(entry);
	return true;
}

obs_data_t *list_scenes()
{
	obs_data_t *result = obs_data_create();
	obs_data_array_t *scenes = obs_data_array_create();
	obs_frontend_source_list source_list{};
	obs_frontend_get_scenes(&source_list);
	const size_t source_count = source_list.sources.num;
	const size_t count = std::min(source_count, kMaxScenes);
	for (size_t index = 0; index < count; ++index) {
		obs_source_t *source = source_list.sources.array[index];
		obs_data_t *entry = obs_data_create();
		obs_data_set_string(entry, "sceneName", obs_source_get_name(source));
		obs_data_array_push_back(scenes, entry);
		obs_data_release(entry);
	}
	obs_frontend_source_list_free(&source_list);

	obs_source_t *current = obs_frontend_get_current_scene();
	if (current != nullptr) {
		obs_data_set_string(result, "currentSceneName", obs_source_get_name(current));
		obs_source_release(current);
	}
	obs_data_set_array(result, "scenes", scenes);
	obs_data_set_bool(result, "truncated", source_count > kMaxScenes);
	obs_data_array_release(scenes);
	return result;
}

obs_data_t *list_sources(const std::string &scene_name)
{
	obs_data_t *result = obs_data_create();
	obs_source_t *scene_source = scene_name.empty() ? obs_frontend_get_current_scene()
							: obs_get_source_by_name(scene_name.c_str());
	if (scene_source == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	obs_scene_t *scene = obs_scene_from_source(scene_source);
	if (scene == nullptr) {
		obs_source_release(scene_source);
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}

	obs_data_array_t *sources = obs_data_array_create();
	obs_scene_enum_items(scene, append_scene_item, sources);
	obs_data_set_string(result, "sceneName", obs_source_get_name(scene_source));
	obs_data_set_array(result, "sources", sources);
	obs_data_set_bool(result, "truncated", obs_data_array_count(sources) >= kMaxSources);
	obs_data_array_release(sources);
	obs_source_release(scene_source);
	return result;
}

obs_data_t *recording_status()
{
	obs_data_t *result = obs_data_create();
	obs_data_set_bool(result, "outputActive", obs_frontend_recording_active());
	obs_data_set_bool(result, "outputPaused", obs_frontend_recording_paused());
	return result;
}

void execute_ui_operation(void *private_data)
{
	std::unique_ptr<std::shared_ptr<UiState>> holder(static_cast<std::shared_ptr<UiState> *>(private_data));
	std::shared_ptr<UiState> state = *holder;
	obs_data_t *result = nullptr;
	const bool is_mutation =
		state->operation == UiOperation::StartRecording || state->operation == UiOperation::StopRecording ||
		state->operation == UiOperation::PauseRecording || state->operation == UiOperation::ResumeRecording;
	if (!is_mutation && !state->gate.try_start()) {
		result = obs_data_create();
		set_error(result, "OBS_UI_TIMEOUT");
	}

	if (result == nullptr) {
		switch (state->operation) {
		case UiOperation::Status:
			result = obs_data_create();
			obs_data_set_bool(result, "ready", true);
			break;
		case UiOperation::ListScenes:
			result = list_scenes();
			break;
		case UiOperation::ListSources:
			result = list_sources(state->scene_name);
			break;
		case UiOperation::RecordingStatus:
			result = recording_status();
			break;
		case UiOperation::StartRecording: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			if (!state->gate.claim_mutation()) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				if (!recording_active)
					obs_frontend_recording_start();
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::StopRecording: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			if (!state->gate.claim_mutation()) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				if (recording_active)
					obs_frontend_recording_stop();
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::PauseRecording: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			const bool recording_paused = recording_active && obs_frontend_recording_paused();
			if (!state->gate.claim_mutation()) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else if (!recording_active) {
				set_error(result, "OBS_RECORDING_NOT_ACTIVE");
			} else {
				if (!recording_paused)
					obs_frontend_recording_pause(true);
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::ResumeRecording: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			const bool recording_paused = recording_active && obs_frontend_recording_paused();
			if (!state->gate.claim_mutation()) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else if (!recording_active) {
				set_error(result, "OBS_RECORDING_NOT_ACTIVE");
			} else {
				if (recording_paused)
					obs_frontend_recording_pause(false);
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		}
	}

	const uint64_t response_sequence = g_event_sequence.fetch_add(1) + 1;
	set_identity(result, response_sequence);
	if (!obs_data_has_user_value(result, "ok"))
		obs_data_set_bool(result, "ok", true);
	{
		std::lock_guard<std::mutex> lock(state->mutex);
		state->result = result;
		state->complete = true;
	}
	state->condition.notify_one();
}

bool run_ui_operation(UiOperation operation, const std::string &scene_name, obs_data_t *response)
{
	auto state = std::make_shared<UiState>();
	state->operation = operation;
	state->scene_name = scene_name;
	auto *holder = new std::shared_ptr<UiState>(state);
	obs_queue_task(OBS_TASK_UI, execute_ui_operation, holder, false);

	std::unique_lock<std::mutex> lock(state->mutex);
	if (!state->condition.wait_for(lock, kUiTimeout, [&state] { return state->complete; })) {
		state->gate.cancel_pending();
		set_error(response, "OBS_UI_TIMEOUT");
		return false;
	}
	obs_data_apply(response, state->result);
	return obs_data_get_bool(response, "ok");
}

UiOperation operation_for(const std::string &request)
{
	if (request == "GetPluginStatus")
		return UiOperation::Status;
	if (request == "ListScenes")
		return UiOperation::ListScenes;
	if (request == "ListSources")
		return UiOperation::ListSources;
	if (request == "GetRecordingStatus")
		return UiOperation::RecordingStatus;
	if (request == "StartRecording")
		return UiOperation::StartRecording;
	if (request == "StopRecording")
		return UiOperation::StopRecording;
	if (request == "PauseRecording")
		return UiOperation::PauseRecording;
	return UiOperation::ResumeRecording;
}

void vendor_request(obs_data_t *request_data, obs_data_t *response_data, void *private_data)
{
	const auto *request = static_cast<const char *>(private_data);
	std::string scene_name;
	if (std::string(request) == "ListSources" && request_data != nullptr) {
		const char *value = obs_data_get_string(request_data, "sceneName");
		if (value != nullptr)
			scene_name = value;
		if (scene_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	run_ui_operation(operation_for(request), scene_name, response_data);
}

void frontend_event(enum obs_frontend_event, void *)
{
	const uint64_t event_sequence = g_event_sequence.fetch_add(1) + 1;
	if (g_vendor != nullptr) {
		obs_data_t *event = obs_data_create();
		set_identity(event, event_sequence);
		obs_websocket_vendor_emit_event(g_vendor, "HostStateChanged", event);
		obs_data_release(event);
	}
}

constexpr const char *kRequests[] = {
	"GetPluginStatus", "ListScenes",    "ListSources",    "GetRecordingStatus",
	"StartRecording",  "StopRecording", "PauseRecording", "ResumeRecording",
};

} // namespace

const char *obs_module_description(void)
{
	return "Native typed DCC-MCP control bridge for OBS Studio";
}

bool obs_module_load(void)
{
	g_instance_id = make_instance_id();
	obs_frontend_add_event_callback(frontend_event, nullptr);
	blog(LOG_INFO, "dcc-mcp-obs native plugin loaded");
	return true;
}

void obs_module_post_load(void)
{
	g_vendor = obs_websocket_register_vendor(kVendorName);
	if (g_vendor == nullptr) {
		blog(LOG_ERROR, "dcc-mcp-obs requires obs-websocket API v3");
		return;
	}
	for (const char *request : kRequests)
		obs_websocket_vendor_register_request(g_vendor, request, vendor_request, const_cast<char *>(request));
}

void obs_module_unload(void)
{
	obs_frontend_remove_event_callback(frontend_event, nullptr);
	if (g_vendor != nullptr) {
		for (const char *request : kRequests)
			obs_websocket_vendor_unregister_request(g_vendor, request);
	}
	g_vendor = nullptr;
	blog(LOG_INFO, "dcc-mcp-obs native plugin unloaded");
}
