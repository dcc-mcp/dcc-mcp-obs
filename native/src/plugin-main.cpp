#include <obs-frontend-api.h>
#include <obs-module.h>
#include <util/platform.h>

#include <QMetaObject>
#include <QMainWindow>
#include <QDesktopServices>
#include <QMessageBox>
#include <QUrl>
#include <QWidget>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <memory>
#include <mutex>
#include <limits>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#include <Windows.h>
#else
#include <unistd.h>
#endif

#include "obs-websocket-api.h"
#include "agent-input-overlay.hpp"
#include "dcc-mcp-menu.hpp"
#include "scene-recording-session.hpp"
#include "sidecar-launcher.hpp"
#include "typed-source-control.hpp"
#include "ui-task-gate.hpp"

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("dcc-mcp-obs", "en-US")

namespace {

constexpr char kVendorName[] = "dcc-mcp-obs";
constexpr char kPluginVersion[] = "1.1.0"; // x-release-please-version
constexpr auto kUiTimeout = std::chrono::seconds(5);
constexpr size_t kMaxScenes = 256;
constexpr size_t kMaxTransitions = 128;
constexpr size_t kMaxSources = 512;
#ifdef _WIN32
constexpr size_t kMaxWindowCaptureCandidates = 64;
#endif
constexpr size_t kMaxOutputs = 8;
constexpr size_t kMaxProfiles = 128;
constexpr size_t kMaxSceneCollections = 128;
constexpr size_t kMaxProgramFrameDataUrlBytes = 400000;
constexpr unsigned int kObsWebSocketSuccessStatus = 100;

// This is deliberately a small, reviewable allowlist.  Callers can only
// trigger these named actions; arbitrary OBS hotkey ids/names are never
// accepted from the vendor transport.
constexpr const char *kAllowlistedHotkeys[] = {
	"start_recording",
	"stop_recording",
	"start_streaming",
	"stop_streaming",
	"start_replay_buffer",
	"stop_replay_buffer",
	"start_virtual_camera",
	"stop_virtual_camera",
	"OBSBasic.StartRecording",
	"OBSBasic.StopRecording",
	"OBSBasic.StartStreaming",
	"OBSBasic.StopStreaming",
	"OBSBasic.StartReplayBuffer",
	"OBSBasic.StopReplayBuffer",
	"OBSBasic.StartVirtualCam",
	"OBSBasic.StopVirtualCam",
};

obs_websocket_vendor g_vendor = nullptr;
std::atomic<uint64_t> g_event_sequence{0};
std::string g_instance_id;
std::atomic<bool> g_plugin_loaded{false};
std::unique_ptr<dcc_mcp_obs::DccMcpMenu> g_dcc_mcp_menu;
std::unique_ptr<dcc_mcp_obs::SidecarLauncher> g_sidecar_launcher;
std::unique_ptr<dcc_mcp_obs::SceneRecordingSessionManager> g_scene_recording_sessions;

QMainWindow *obs_main_window()
{
	return static_cast<QMainWindow *>(obs_frontend_get_main_window());
}

void show_dcc_mcp_server_status()
{
	dcc_mcp_obs::DccMcpMenuStatus status;
	status.plugin_version = kPluginVersion;
	status.obs_version = obs_get_version_string();
	status.native_bridge_ready = g_vendor != nullptr;
	status.recording_active = obs_frontend_recording_active();
	status.streaming_active = obs_frontend_streaming_active();
	status.replay_buffer_active = obs_frontend_replay_buffer_active();
	status.virtual_camera_active = obs_frontend_virtualcam_active();
	if (auto *scene = obs_frontend_get_current_scene()) {
		const char *name = obs_source_get_name(scene);
		if (name != nullptr)
			status.scene_name = name;
		obs_source_release(scene);
	}
	QMessageBox::information(obs_main_window(), QStringLiteral("DCC-MCP OBS — Server Status"),
				 QString::fromStdString(dcc_mcp_obs::format_dcc_mcp_menu_status(status)));
}

void add_agent_input_overlay_from_menu()
{
	auto *scene = obs_frontend_get_current_scene();
	if (scene == nullptr) {
		QMessageBox::warning(obs_main_window(), QStringLiteral("DCC-MCP OBS"),
				     QStringLiteral("No current OBS scene is available."));
		return;
	}
	const char *name = obs_source_get_name(scene);
	const std::string scene_name = name != nullptr ? name : "";
	obs_source_release(scene);
	if (scene_name.empty()) {
		QMessageBox::warning(obs_main_window(), QStringLiteral("DCC-MCP OBS"),
				     QStringLiteral("The current OBS scene has no stable name."));
		return;
	}
	auto *result = dcc_mcp_obs::create_agent_input_overlay(
		scene_name, dcc_mcp_obs::kDefaultAgentInputOverlaySourceName, "bottom_right");
	const bool accepted = result != nullptr && obs_data_get_bool(result, "accepted");
	const QString error = result != nullptr ? QString::fromUtf8(obs_data_get_string(result, "errorCode"))
						: QStringLiteral("OBS_REQUEST_FAILED");
	if (result != nullptr)
		obs_data_release(result);
	if (!accepted) {
		QMessageBox::warning(obs_main_window(), QStringLiteral("DCC-MCP OBS"),
				     QStringLiteral("Could not add the Agent Input Overlay: %1")
					     .arg(error.isEmpty() ? QStringLiteral("OBS_REQUEST_FAILED") : error));
		return;
	}
	QMessageBox::information(obs_main_window(), QStringLiteral("DCC-MCP OBS"),
				 QStringLiteral("Agent Input Overlay is attached to scene “%1”.")
					 .arg(QString::fromStdString(scene_name)));
}

int gateway_admin_port()
{
	const char *raw = std::getenv("DCC_MCP_GATEWAY_PORT");
	if (raw == nullptr || *raw == '\0')
		return 9765;
	char *end = nullptr;
	const long parsed = std::strtol(raw, &end, 10);
	return end != raw && *end == '\0' && parsed > 0 && parsed <= 65535 ? static_cast<int>(parsed) : 9765;
}

void open_dcc_mcp_gateway_admin()
{
	const QUrl url(QStringLiteral("http://127.0.0.1:%1/admin?panel=instances").arg(gateway_admin_port()));
	if (!QDesktopServices::openUrl(url))
		QMessageBox::warning(obs_main_window(), QStringLiteral("DCC-MCP OBS"),
				     QStringLiteral("The Gateway Admin URL could not be opened."));
}

void show_dcc_mcp_about()
{
	QMessageBox::information(
		obs_main_window(), QStringLiteral("About DCC-MCP OBS"),
		QStringLiteral(
			"DCC-MCP OBS %1\n\nNative typed OBS control, Agent input presentation, and exact lifecycle verification.")
			.arg(QString::fromUtf8(kPluginVersion)));
}

void install_dcc_mcp_menu(void *)
{
	if (!g_plugin_loaded.load())
		return;
	auto *main_window = obs_main_window();
	if (main_window == nullptr) {
		blog(LOG_WARNING, "dcc-mcp-obs could not find the OBS main window for menu registration");
		return;
	}
	if (g_dcc_mcp_menu == nullptr) {
		dcc_mcp_obs::DccMcpMenuCallbacks callbacks{
			show_dcc_mcp_server_status,
			add_agent_input_overlay_from_menu,
			open_dcc_mcp_gateway_admin,
			show_dcc_mcp_about,
		};
		g_dcc_mcp_menu = std::make_unique<dcc_mcp_obs::DccMcpMenu>(main_window, std::move(callbacks));
	}
	if (!g_dcc_mcp_menu->install())
		blog(LOG_WARNING, "dcc-mcp-obs could not register the DCC MCP menu");
}

void remove_dcc_mcp_menu(void *)
{
	if (g_dcc_mcp_menu == nullptr)
		return;
	g_dcc_mcp_menu->remove();
	g_dcc_mcp_menu.reset();
}

void start_configured_sidecar(void *)
{
	if (!g_plugin_loaded.load())
		return;
	if (g_sidecar_launcher == nullptr) {
		g_sidecar_launcher = std::make_unique<dcc_mcp_obs::SidecarLauncher>([](const std::string &error) {
			blog(LOG_ERROR, "dcc-mcp-obs sidecar process error: %s", error.c_str());
		});
	}
	const auto result = g_sidecar_launcher->start_from_environment();
	switch (result.state) {
	case dcc_mcp_obs::SidecarLaunchState::Disabled:
		blog(LOG_INFO, "dcc-mcp-obs sidecar autostart is disabled; set %s to an absolute standalone executable",
		     dcc_mcp_obs::kObsExecutableEnvironment.data());
		break;
	case dcc_mcp_obs::SidecarLaunchState::Starting:
		blog(LOG_INFO, "dcc-mcp-obs starting sidecar: %s", result.executable.c_str());
		break;
	case dcc_mcp_obs::SidecarLaunchState::AlreadyRunning:
		break;
	case dcc_mcp_obs::SidecarLaunchState::InvalidExecutable:
		blog(LOG_ERROR, "%s is invalid: %s", dcc_mcp_obs::kObsExecutableEnvironment.data(),
		     result.message.c_str());
		break;
	}
}

void stop_configured_sidecar(void *)
{
	g_sidecar_launcher.reset();
}

void stop_scene_recording_sessions(void *)
{
	if (g_scene_recording_sessions != nullptr)
		g_scene_recording_sessions->shutdown();
	g_scene_recording_sessions.reset();
}

enum class UiOperation {
	Invalid,
	Status,
	OperatorStatus,
	ListScenes,
	ListSources,
	CreateAgentInputOverlay,
	GetAgentInputOverlay,
	SetAgentInputOverlayLayout,
	EmitAgentInputActivity,
	ClearAgentInputOverlay,
	RequestGracefulShutdown,
	RecordingStatus,
	StartRecording,
	StopRecording,
	PauseRecording,
	ResumeRecording,
	StartSceneRecordings,
	GetSceneRecordingSession,
	StopSceneRecordings,
	StreamingStatus,
	StartStreaming,
	StopStreaming,
	ReplayBufferStatus,
	StartReplayBuffer,
	StopReplayBuffer,
	SaveReplayBuffer,
	VirtualCameraStatus,
	StartVirtualCamera,
	StopVirtualCamera,
	ListOutputs,
	OutputStatus,
	StartOutput,
	StopOutput,
	ListProfiles,
	ProfileStatus,
	SetProfile,
	ListSceneCollections,
	SceneCollectionStatus,
	SetSceneCollection,
	ListAllowlistedHotkeys,
	TriggerAllowlistedHotkey,
	CaptureScreenshot,
	CaptureProgramFrame,
	SetCurrentScene,
	GetCurrentScene,
	CreateScene,
	RenameScene,
	RemoveScene,
	ListSceneItems,
	GetSceneItem,
	CreateSceneItem,
	ListWindowCaptureCandidates,
	RestoreWindowCaptureCandidate,
	CreateWindowCaptureSource,
	GetWindowCaptureSource,
	RebindWindowCaptureSource,
	SetWindowCaptureMethod,
	SetSceneItemEnabled,
	SetSceneItemTransform,
	RemoveSceneItem,
	ListTransitions,
	GetCurrentTransition,
	SetCurrentTransition,
	TriggerTransition,
	GetStudioModeStatus,
	SetStudioMode,
	GetCurrentPreviewScene,
	SetCurrentPreviewScene,
	TriggerStudioModeTransition,
	TypedSourceControl,
};

struct WindowCaptureBinding {
	uint32_t process_id = 0;
	uint64_t window_handle = 0;
	std::string window_title;
	std::string capture_method = "automatic";
	bool capture_cursor = true;
	bool client_area = true;
};

struct UiState {
	UiOperation operation;
	std::string scene_name;
	std::string output_name;
	std::string target_name;
	std::string hotkey_name;
	std::string source_name;
	std::string source_kind;
	std::string transition_name;
	std::string overlay_anchor;
	int overlay_opacity = 78;
	int overlay_margin = 48;
	dcc_mcp_obs::AgentInputActivity agent_input_activity;
	dcc_mcp_obs::TypedSourceRequest typed_source_request;
	std::vector<dcc_mcp_obs::SceneRecordingSpec> scene_recording_specs;
	std::string scene_recording_session_id;
	std::string window_executable_filter;
	std::string window_title_filter;
	WindowCaptureBinding window_capture;
	WindowCaptureBinding expected_window_capture;
	int64_t scene_item_id = 0;
	bool enabled = true, studio_enabled = false, has_duration = false;
	int duration_ms = 0;
	bool has_pos = false, has_scale = false, has_rotation = false;
	float pos_x = 0.0f, pos_y = 0.0f, scale_x = 1.0f, scale_y = 1.0f, rotation = 0.0f;
	std::string image_format;
	std::chrono::steady_clock::time_point deadline;
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

enum class NameLookup { Missing, Unique, Ambiguous, Incomplete };

NameLookup lookup_name(char **names, const std::string &target, size_t limit)
{
	if (names == nullptr || target.empty())
		return NameLookup::Missing;
	size_t matches = 0;
	size_t count = 0;
	for (; count < limit && names[count] != nullptr; ++count) {
		if (target == names[count])
			++matches;
	}
	if (count >= limit && names[count] != nullptr)
		return NameLookup::Incomplete;
	if (matches > 1)
		return NameLookup::Ambiguous;
	return matches == 1 ? NameLookup::Unique : NameLookup::Missing;
}

obs_data_t *list_profiles()
{
	auto *result = obs_data_create();
	auto *profiles = obs_data_array_create();
	char **names = obs_frontend_get_profiles();
	size_t count = 0;
	if (names != nullptr) {
		for (; count <= kMaxProfiles && names[count] != nullptr; ++count) {
			if (count < kMaxProfiles) {
				auto *entry = obs_data_create();
				obs_data_set_string(entry, "profileName", names[count]);
				obs_data_array_push_back(profiles, entry);
				obs_data_release(entry);
			}
		}
		bfree(names);
	}
	char *current = obs_frontend_get_current_profile();
	if (current != nullptr) {
		obs_data_set_string(result, "currentProfileName", current);
		bfree(current);
	}
	obs_data_set_array(result, "profiles", profiles);
	obs_data_set_bool(result, "truncated", count > kMaxProfiles);
	obs_data_array_release(profiles);
	return result;
}

obs_data_t *profile_status()
{
	auto *result = obs_data_create();
	char *current = obs_frontend_get_current_profile();
	if (current == nullptr || !*current) {
		if (current != nullptr)
			bfree(current);
		set_error(result, "OBS_PROFILE_NOT_FOUND");
		return result;
	}
	obs_data_set_string(result, "profileName", current);
	bfree(current);
	return result;
}

obs_data_t *list_scene_collections()
{
	auto *result = obs_data_create();
	auto *collections = obs_data_array_create();
	char **names = obs_frontend_get_scene_collections();
	size_t count = 0;
	if (names != nullptr) {
		for (; count <= kMaxSceneCollections && names[count] != nullptr; ++count) {
			if (count < kMaxSceneCollections) {
				auto *entry = obs_data_create();
				obs_data_set_string(entry, "sceneCollectionName", names[count]);
				obs_data_array_push_back(collections, entry);
				obs_data_release(entry);
			}
		}
		bfree(names);
	}
	char *current = obs_frontend_get_current_scene_collection();
	if (current != nullptr) {
		obs_data_set_string(result, "currentSceneCollectionName", current);
		bfree(current);
	}
	obs_data_set_array(result, "sceneCollections", collections);
	obs_data_set_bool(result, "truncated", count > kMaxSceneCollections);
	obs_data_array_release(collections);
	return result;
}

obs_data_t *scene_collection_status()
{
	auto *result = obs_data_create();
	char *current = obs_frontend_get_current_scene_collection();
	if (current == nullptr || !*current) {
		if (current != nullptr)
			bfree(current);
		set_error(result, "OBS_SCENE_COLLECTION_NOT_FOUND");
		return result;
	}
	obs_data_set_string(result, "sceneCollectionName", current);
	bfree(current);
	return result;
}

bool allowlisted_hotkey(const std::string &name)
{
	for (const char *entry : kAllowlistedHotkeys) {
		if (name == entry)
			return true;
	}
	return false;
}

bool valid_agent_input_key(const std::string &key)
{
	static constexpr const char *named_keys[] = {"ctrl",   "shift", "alt",   "meta",      "enter",
						     "escape", "tab",   "space", "backspace", "delete",
						     "up",     "down",  "left",  "right"};
	for (const char *named : named_keys) {
		if (key == named)
			return true;
	}
	if (key.size() == 1)
		return (key[0] >= 'a' && key[0] <= 'z') || (key[0] >= '0' && key[0] <= '9');
	if (key.size() >= 2 && key[0] == 'f') {
		try {
			const int number = std::stoi(key.substr(1));
			return number >= 1 && number <= 12 && key == "f" + std::to_string(number);
		} catch (...) {
			return false;
		}
	}
	return false;
}

obs_data_t *list_allowlisted_hotkeys()
{
	auto *result = obs_data_create();
	auto *hotkeys = obs_data_array_create();
	for (const char *name : kAllowlistedHotkeys) {
		auto *entry = obs_data_create();
		obs_data_set_string(entry, "hotkeyName", name);
		obs_data_array_push_back(hotkeys, entry);
		obs_data_release(entry);
	}
	obs_data_set_array(result, "hotkeys", hotkeys);
	obs_data_set_bool(result, "truncated", false);
	obs_data_array_release(hotkeys);
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

obs_source_t *scene_source_by_name(const std::string &name)
{
	if (name.empty())
		return nullptr;
	obs_source_t *source = obs_get_source_by_name(name.c_str());
	if (source == nullptr || !obs_source_is_scene(source)) {
		if (source != nullptr)
			obs_source_release(source);
		return nullptr;
	}
	return source;
}

obs_data_t *current_scene_status()
{
	auto *result = obs_data_create();
	auto *scene = obs_frontend_get_current_scene();
	if (scene == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	obs_data_set_string(result, "sceneName", obs_source_get_name(scene));
	obs_source_release(scene);
	return result;
}

void set_scene_item_data(obs_data_t *entry, const std::string &scene_name, obs_sceneitem_t *item)
{
	obs_source_t *source = obs_sceneitem_get_source(item);
	if (source == nullptr)
		return;
	obs_data_set_string(entry, "sceneName", scene_name.c_str());
	obs_data_set_int(entry, "sceneItemId", obs_sceneitem_get_id(item));
	obs_data_set_string(entry, "sourceName", obs_source_get_name(source));
	obs_data_set_string(entry, "sourceKind", obs_source_get_id(source));
	obs_data_set_bool(entry, "enabled", obs_sceneitem_visible(item));
	vec2 pos{}, scale{};
	obs_sceneitem_get_pos(item, &pos);
	obs_sceneitem_get_scale(item, &scale);
	obs_data_set_double(entry, "posX", pos.x);
	obs_data_set_double(entry, "posY", pos.y);
	obs_data_set_double(entry, "scaleX", scale.x);
	obs_data_set_double(entry, "scaleY", scale.y);
	obs_data_set_double(entry, "rotation", obs_sceneitem_get_rot(item));
}

obs_data_t *list_scene_items(const std::string &scene_name)
{
	auto *result = obs_data_create();
	auto *scene_source = scene_name.empty() ? obs_frontend_get_current_scene() : scene_source_by_name(scene_name);
	if (scene_source == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	auto *scene = obs_scene_from_source(scene_source);
	if (scene == nullptr) {
		obs_source_release(scene_source);
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	auto *items = obs_data_array_create();
	struct Context {
		obs_data_array_t *items;
		const char *scene_name;
	} context{items, obs_source_get_name(scene_source)};
	obs_scene_enum_items(
		scene,
		[](obs_scene_t *, obs_sceneitem_t *item, void *private_data) {
			auto *ctx = static_cast<Context *>(private_data);
			if (obs_data_array_count(ctx->items) >= kMaxSources)
				return false;
			auto *entry = obs_data_create();
			set_scene_item_data(entry, ctx->scene_name, item);
			obs_data_array_push_back(ctx->items, entry);
			obs_data_release(entry);
			return true;
		},
		&context);
	obs_data_set_string(result, "sceneName", obs_source_get_name(scene_source));
	obs_data_set_array(result, "sceneItems", items);
	obs_data_set_bool(result, "truncated", obs_data_array_count(items) >= kMaxSources);
	obs_data_array_release(items);
	obs_source_release(scene_source);
	return result;
}

obs_data_t *scene_item_status(const std::string &scene_name, int64_t item_id)
{
	auto *result = obs_data_create();
	auto *scene_source = scene_source_by_name(scene_name);
	if (scene_source == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	auto *scene = obs_scene_from_source(scene_source);
	auto *item = scene == nullptr ? nullptr : obs_scene_find_sceneitem_by_id(scene, item_id);
	if (item == nullptr) {
		obs_source_release(scene_source);
		obs_data_set_string(result, "sceneName", scene_name.c_str());
		obs_data_set_int(result, "sceneItemId", item_id);
		obs_data_set_bool(result, "exists", false);
		return result;
	}
	set_scene_item_data(result, scene_name, item);
	obs_source_release(scene_source);
	return result;
}

constexpr char kCaptureMethodAutomatic[] = "automatic";
constexpr char kCaptureMethodBitBlt[] = "bitblt";
constexpr char kCaptureMethodWgc[] = "windows_graphics_capture";

int window_capture_method_value(const std::string &method)
{
	if (method == kCaptureMethodAutomatic)
		return 0;
	if (method == kCaptureMethodBitBlt)
		return 1;
	if (method == kCaptureMethodWgc)
		return 2;
	return -1;
}

#ifdef _WIN32
constexpr char kWindowBindingSchema[] = "windows-window-v1";

struct ValidatedWindowBinding {
	HANDLE process_handle = nullptr;
	uint64_t process_created = 0;
	std::string title;
	std::string window_class;
	std::string executable;
	std::string selector;

	ValidatedWindowBinding() = default;
	ValidatedWindowBinding(const ValidatedWindowBinding &) = delete;
	ValidatedWindowBinding &operator=(const ValidatedWindowBinding &) = delete;

	~ValidatedWindowBinding()
	{
		if (process_handle != nullptr)
			CloseHandle(process_handle);
	}
};

std::string utf8_from_wide(const std::wstring &value)
{
	if (value.empty())
		return {};
	const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
					     static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
	if (size <= 0)
		return {};
	std::string result(static_cast<size_t>(size), '\0');
	if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
				result.data(), size, nullptr, nullptr) != size)
		return {};
	return result;
}

std::string encode_window_component(const std::string &value)
{
	std::string encoded;
	encoded.reserve(value.size());
	for (const char character : value) {
		if (character == '#')
			encoded += "#22";
		else if (character == ':')
			encoded += "#3A";
		else
			encoded += character;
	}
	return encoded;
}

uint64_t file_time_value(const FILETIME &value)
{
	return (static_cast<uint64_t>(value.dwHighDateTime) << 32) | value.dwLowDateTime;
}

bool read_window_text(HWND window, std::string &title)
{
	const int length = GetWindowTextLengthW(window);
	if (length <= 0 || length > 1024)
		return false;
	std::vector<wchar_t> buffer(static_cast<size_t>(length) + 1);
	if (GetWindowTextW(window, buffer.data(), static_cast<int>(buffer.size())) != length)
		return false;
	title = utf8_from_wide(std::wstring(buffer.data(), static_cast<size_t>(length)));
	return !title.empty() && title.size() <= 256;
}

bool read_window_class(HWND window, std::string &window_class)
{
	std::vector<wchar_t> buffer(512);
	const int length = GetClassNameW(window, buffer.data(), static_cast<int>(buffer.size()));
	if (length <= 0 || length >= static_cast<int>(buffer.size()))
		return false;
	window_class = utf8_from_wide(std::wstring(buffer.data(), static_cast<size_t>(length)));
	return !window_class.empty() && window_class.size() <= 256;
}

bool read_process_identity(HANDLE process, uint64_t &created, std::string &executable)
{
	FILETIME creation{}, exit{}, kernel{}, user{};
	if (!GetProcessTimes(process, &creation, &exit, &kernel, &user))
		return false;
	std::vector<wchar_t> path(32768);
	DWORD path_size = static_cast<DWORD>(path.size());
	if (!QueryFullProcessImageNameW(process, 0, path.data(), &path_size) || path_size == 0)
		return false;
	const std::wstring full_path(path.data(), path_size);
	const size_t separator = full_path.find_last_of(L"\\/");
	const std::wstring base_name = separator == std::wstring::npos ? full_path : full_path.substr(separator + 1);
	executable = utf8_from_wide(base_name);
	created = file_time_value(creation);
	return created > 0 && !executable.empty() && executable.size() <= 256;
}

struct WindowCaptureCandidateScan {
	const std::string &executable_filter;
	const std::string &title_filter;
	obs_data_array_t *candidates;
	size_t matches = 0;
	bool truncated = false;
};

void set_window_capture_candidate_data(obs_data_t *entry, HWND window, DWORD process_id, const std::string &title,
				       const std::string &window_class, const std::string &executable)
{
	RECT client{};
	const bool client_read = GetClientRect(window, &client) != FALSE;
	const long client_width = client_read ? std::max<long>(0, client.right - client.left) : 0;
	const long client_height = client_read ? std::max<long>(0, client.bottom - client.top) : 0;
	const bool visible = IsWindowVisible(window) != FALSE;
	const bool minimized = IsIconic(window) != FALSE;
	obs_data_set_int(entry, "processId", process_id);
	obs_data_set_int(entry, "windowHandle", static_cast<long long>(reinterpret_cast<uintptr_t>(window)));
	obs_data_set_string(entry, "windowTitle", title.c_str());
	obs_data_set_string(entry, "windowClass", window_class.c_str());
	obs_data_set_string(entry, "executable", executable.c_str());
	obs_data_set_bool(entry, "visible", visible);
	obs_data_set_bool(entry, "minimized", minimized);
	obs_data_set_int(entry, "clientWidth", client_width);
	obs_data_set_int(entry, "clientHeight", client_height);
	obs_data_set_bool(entry, "captureReady", visible && !minimized && client_width > 0 && client_height > 0);
}

BOOL CALLBACK append_window_capture_candidate(HWND window, LPARAM private_data)
{
	auto *scan = reinterpret_cast<WindowCaptureCandidateScan *>(private_data);
	if (!IsWindowVisible(window))
		return TRUE;
	DWORD process_id = 0;
	std::string title, window_class, executable;
	uint64_t process_created = 0;
	if (GetWindowThreadProcessId(window, &process_id) == 0 || process_id == 0 || !read_window_text(window, title) ||
	    !read_window_class(window, window_class))
		return TRUE;
	HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
	if (process == nullptr)
		return TRUE;
	const bool identity_read = read_process_identity(process, process_created, executable);
	CloseHandle(process);
	if (!identity_read || _stricmp(executable.c_str(), scan->executable_filter.c_str()) != 0 ||
	    (!scan->title_filter.empty() && title != scan->title_filter))
		return TRUE;
	if (scan->matches >= kMaxWindowCaptureCandidates) {
		scan->truncated = true;
		return FALSE;
	}
	auto *entry = obs_data_create();
	set_window_capture_candidate_data(entry, window, process_id, title, window_class, executable);
	obs_data_array_push_back(scan->candidates, entry);
	obs_data_release(entry);
	++scan->matches;
	return TRUE;
}

obs_data_t *list_window_capture_candidates(const std::string &executable_filter, const std::string &title_filter)
{
	auto *result = obs_data_create();
	auto *candidates = obs_data_array_create();
	WindowCaptureCandidateScan scan{executable_filter, title_filter, candidates};
	EnumWindows(append_window_capture_candidate, reinterpret_cast<LPARAM>(&scan));
	obs_data_set_string(result, "executable", executable_filter.c_str());
	if (!title_filter.empty())
		obs_data_set_string(result, "windowTitle", title_filter.c_str());
	obs_data_set_array(result, "candidates", candidates);
	obs_data_set_bool(result, "truncated", scan.truncated);
	obs_data_array_release(candidates);
	return result;
}

const char *validate_exact_window(const WindowCaptureBinding &expected, ValidatedWindowBinding &actual)
{
	HWND window = reinterpret_cast<HWND>(static_cast<uintptr_t>(expected.window_handle));
	if (!IsWindow(window) || !IsWindowVisible(window))
		return "OBS_WINDOW_NOT_FOUND";
	DWORD process_id = 0;
	if (GetWindowThreadProcessId(window, &process_id) == 0 || process_id != expected.process_id)
		return "OBS_WINDOW_IDENTITY_DRIFT";
	if (!read_window_text(window, actual.title) || actual.title != expected.window_title ||
	    !read_window_class(window, actual.window_class))
		return "OBS_WINDOW_IDENTITY_DRIFT";
	actual.process_handle =
		OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, expected.process_id);
	if (actual.process_handle == nullptr ||
	    !read_process_identity(actual.process_handle, actual.process_created, actual.executable))
		return "OBS_WINDOW_IDENTITY_DRIFT";
	actual.selector = encode_window_component(actual.title) + ":" + encode_window_component(actual.window_class) +
			  ":" + encode_window_component(actual.executable);
	return nullptr;
}

bool exact_window_is_still_live(const WindowCaptureBinding &expected, const ValidatedWindowBinding &actual)
{
	DWORD exit_code = 0;
	if (!GetExitCodeProcess(actual.process_handle, &exit_code) || exit_code != STILL_ACTIVE)
		return false;
	HWND window = reinterpret_cast<HWND>(static_cast<uintptr_t>(expected.window_handle));
	DWORD process_id = 0;
	std::string title, window_class, executable;
	uint64_t process_created = 0;
	return IsWindow(window) && IsWindowVisible(window) && GetWindowThreadProcessId(window, &process_id) != 0 &&
	       process_id == expected.process_id && read_window_text(window, title) && title == actual.title &&
	       read_window_class(window, window_class) && window_class == actual.window_class &&
	       read_process_identity(actual.process_handle, process_created, executable) &&
	       process_created == actual.process_created && executable == actual.executable;
}
#endif

#ifdef _WIN32
struct SceneSourceMatch {
	const std::string &source_name;
	obs_sceneitem_t *item = nullptr;
	size_t count = 0;
};

bool find_scene_source_item(obs_scene_t *, obs_sceneitem_t *item, void *private_data)
{
	auto *match = static_cast<SceneSourceMatch *>(private_data);
	auto *source = obs_sceneitem_get_source(item);
	if (source != nullptr && match->source_name == obs_source_get_name(source)) {
		match->item = item;
		++match->count;
	}
	return match->count < 2;
}

bool window_capture_settings_match(obs_data_t *settings, const UiState &state, const ValidatedWindowBinding &actual,
				   bool include_method)
{
	return settings != nullptr &&
	       std::string(obs_data_get_string(settings, "_dcc_binding_schema")) == kWindowBindingSchema &&
	       obs_data_get_int(settings, "_dcc_process_id") == state.window_capture.process_id &&
	       obs_data_get_int(settings, "_dcc_window_handle") ==
		       static_cast<long long>(state.window_capture.window_handle) &&
	       obs_data_get_int(settings, "_dcc_process_created") == static_cast<long long>(actual.process_created) &&
	       std::string(obs_data_get_string(settings, "_dcc_window_title")) == actual.title &&
	       std::string(obs_data_get_string(settings, "_dcc_window_class")) == actual.window_class &&
	       std::string(obs_data_get_string(settings, "_dcc_executable")) == actual.executable &&
	       std::string(obs_data_get_string(settings, "window")) == actual.selector &&
	       obs_data_get_bool(settings, "cursor") == state.window_capture.capture_cursor &&
	       obs_data_get_bool(settings, "client_area") == state.window_capture.client_area &&
	       (!include_method || obs_data_get_int(settings, "method") ==
					   window_capture_method_value(state.window_capture.capture_method));
}

bool stored_window_capture_identity_matches(obs_data_t *settings, const WindowCaptureBinding &expected)
{
	return settings != nullptr &&
	       std::string(obs_data_get_string(settings, "_dcc_binding_schema")) == kWindowBindingSchema &&
	       obs_data_get_int(settings, "_dcc_process_id") == expected.process_id &&
	       obs_data_get_int(settings, "_dcc_window_handle") == static_cast<long long>(expected.window_handle) &&
	       std::string(obs_data_get_string(settings, "_dcc_window_title")) == expected.window_title &&
	       obs_data_get_int(settings, "_dcc_process_created") > 0 &&
	       !std::string(obs_data_get_string(settings, "_dcc_window_class")).empty() &&
	       !std::string(obs_data_get_string(settings, "_dcc_executable")).empty() &&
	       !std::string(obs_data_get_string(settings, "window")).empty();
}

bool stored_window_capture_configuration_matches(obs_data_t *settings, const UiState &state)
{
	return settings != nullptr && obs_data_get_bool(settings, "cursor") == state.window_capture.capture_cursor &&
	       obs_data_get_bool(settings, "client_area") == state.window_capture.client_area &&
	       obs_data_get_int(settings, "method") == window_capture_method_value(state.window_capture.capture_method);
}
#endif

obs_data_t *window_capture_source_status(const UiState &state)
{
	auto *result = obs_data_create();
#ifndef _WIN32
	UNUSED_PARAMETER(state);
	set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
	ValidatedWindowBinding actual;
	if (const char *error = validate_exact_window(state.window_capture, actual); error != nullptr) {
		set_error(result, error);
		return result;
	}
	auto *scene_source = scene_source_by_name(state.scene_name);
	auto *source = state.source_name.empty() ? nullptr : obs_get_source_by_name(state.source_name.c_str());
	if (scene_source == nullptr || source == nullptr) {
		set_error(result, scene_source == nullptr ? "OBS_SCENE_NOT_FOUND" : "OBS_SOURCE_NOT_FOUND");
		if (source != nullptr)
			obs_source_release(source);
		if (scene_source != nullptr)
			obs_source_release(scene_source);
		return result;
	}
	if (std::string(obs_source_get_id(source)) != "window_capture") {
		set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
		obs_source_release(source);
		obs_source_release(scene_source);
		return result;
	}
	auto *scene = obs_scene_from_source(scene_source);
	SceneSourceMatch match{state.source_name};
	if (scene != nullptr)
		obs_scene_enum_items(scene, find_scene_source_item, &match);
	if (match.count != 1 || match.item == nullptr) {
		set_error(result, match.count > 1 ? "OBS_TARGET_AMBIGUOUS" : "OBS_SCENE_ITEM_NOT_FOUND");
		obs_source_release(source);
		obs_source_release(scene_source);
		return result;
	}
	auto *settings = obs_source_get_settings(source);
	const bool settings_match = window_capture_settings_match(settings, state, actual, true) &&
				    obs_sceneitem_visible(match.item) == state.enabled;
	if (settings != nullptr)
		obs_data_release(settings);
	if (!settings_match || !exact_window_is_still_live(state.window_capture, actual)) {
		set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
		obs_source_release(source);
		obs_source_release(scene_source);
		return result;
	}
	obs_data_set_string(result, "sceneName", state.scene_name.c_str());
	obs_data_set_int(result, "sceneItemId", obs_sceneitem_get_id(match.item));
	obs_data_set_string(result, "sourceName", state.source_name.c_str());
	obs_data_set_string(result, "sourceKind", "window_capture");
	obs_data_set_bool(result, "enabled", state.enabled);
	obs_data_set_int(result, "processId", state.window_capture.process_id);
	obs_data_set_int(result, "windowHandle", static_cast<long long>(state.window_capture.window_handle));
	obs_data_set_string(result, "windowTitle", actual.title.c_str());
	obs_data_set_string(result, "windowClass", actual.window_class.c_str());
	obs_data_set_string(result, "executable", actual.executable.c_str());
	obs_data_set_bool(result, "captureCursor", state.window_capture.capture_cursor);
	obs_data_set_bool(result, "clientArea", state.window_capture.client_area);
	obs_data_set_string(result, "captureMethod", state.window_capture.capture_method.c_str());
	obs_data_set_bool(result, "bindingVerified", true);
	obs_source_release(source);
	obs_source_release(scene_source);
#endif
	return result;
}

obs_data_t *list_transitions()
{
	auto *result = obs_data_create();
	auto *transitions = obs_data_array_create();
	obs_frontend_source_list source_list{};
	obs_frontend_get_transitions(&source_list);
	const size_t total = source_list.sources.num;
	const size_t count = std::min(total, kMaxTransitions);
	for (size_t i = 0; i < count; ++i) {
		auto *entry = obs_data_create();
		obs_data_set_string(entry, "transitionName", obs_source_get_name(source_list.sources.array[i]));
		obs_data_set_string(entry, "transitionKind", obs_source_get_id(source_list.sources.array[i]));
		obs_data_array_push_back(transitions, entry);
		obs_data_release(entry);
	}
	obs_frontend_source_list_free(&source_list);
	auto *current = obs_frontend_get_current_transition();
	if (current != nullptr) {
		obs_data_set_string(result, "currentTransitionName", obs_source_get_name(current));
		obs_source_release(current);
	}
	obs_data_set_array(result, "transitions", transitions);
	obs_data_set_bool(result, "truncated", total > kMaxTransitions);
	obs_data_array_release(transitions);
	return result;
}

struct FrontendTransitionLookup {
	NameLookup status = NameLookup::Missing;
	obs_source_t *source = nullptr;
};

FrontendTransitionLookup lookup_frontend_transition(const std::string &name)
{
	FrontendTransitionLookup lookup{};
	if (name.empty())
		return lookup;
	obs_frontend_source_list source_list{};
	obs_frontend_get_transitions(&source_list);
	const size_t total = source_list.sources.num;
	const size_t count = std::min(total, kMaxTransitions);
	size_t matches = 0;
	for (size_t index = 0; index < count; ++index) {
		auto *candidate = source_list.sources.array[index];
		if (name != obs_source_get_name(candidate))
			continue;
		++matches;
		if (matches == 1)
			lookup.source = obs_source_get_ref(candidate);
	}
	obs_frontend_source_list_free(&source_list);
	if (total > kMaxTransitions)
		lookup.status = NameLookup::Incomplete;
	else if (matches > 1)
		lookup.status = NameLookup::Ambiguous;
	else if (matches == 1)
		lookup.status = NameLookup::Unique;
	if (lookup.status != NameLookup::Unique && lookup.source != nullptr) {
		obs_source_release(lookup.source);
		lookup.source = nullptr;
	}
	return lookup;
}

obs_data_t *studio_mode_status()
{
	auto *result = obs_data_create();
	obs_data_set_bool(result, "studioModeEnabled", obs_frontend_preview_program_mode_active());
	auto *program = obs_frontend_get_current_scene();
	if (program != nullptr) {
		obs_data_set_string(result, "programSceneName", obs_source_get_name(program));
		obs_source_release(program);
	}
	auto *preview = obs_frontend_get_current_preview_scene();
	if (preview != nullptr) {
		obs_data_set_string(result, "previewSceneName", obs_source_get_name(preview));
		obs_source_release(preview);
	}
	return result;
}

obs_data_t *recording_status()
{
	obs_data_t *result = obs_data_create();
	obs_data_set_bool(result, "outputActive", obs_frontend_recording_active());
	obs_data_set_bool(result, "outputPaused", obs_frontend_recording_paused());
	obs_data_set_string(result, "outputName", "");
	obs_data_set_string(result, "outputKind", "");
	obs_data_set_string(result, "outputPath", "");
	obs_data_set_int(result, "totalBytes", 0);
	obs_data_set_int(result, "totalFrames", 0);
	obs_data_set_string(result, "lastError", "");

	auto *output = obs_frontend_get_recording_output();
	if (output != nullptr) {
		auto set_bounded_string = [result](const char *key, const char *value, size_t max_length) {
			std::string bounded = value != nullptr ? value : "";
			if (bounded.size() > max_length)
				bounded.resize(max_length);
			obs_data_set_string(result, key, bounded.c_str());
		};

		set_bounded_string("outputName", obs_output_get_name(output), 256);
		set_bounded_string("outputKind", obs_output_get_id(output), 256);
		auto *settings = obs_output_get_settings(output);
		if (settings != nullptr) {
			set_bounded_string("outputPath", obs_data_get_string(settings, "path"), 4096);
			obs_data_release(settings);
		}
		const auto total_bytes = std::min<uint64_t>(obs_output_get_total_bytes(output),
							    static_cast<uint64_t>(std::numeric_limits<int64_t>::max()));
		obs_data_set_int(result, "totalBytes", static_cast<long long>(total_bytes));
		obs_data_set_int(result, "totalFrames", std::max(0, obs_output_get_total_frames(output)));
		set_bounded_string("lastError", obs_output_get_last_error(output), 4096);
		obs_output_release(output);
	}
	return result;
}

obs_data_t *streaming_status()
{
	auto *result = obs_data_create();
	obs_data_set_bool(result, "streamingActive", obs_frontend_streaming_active());
	return result;
}

obs_data_t *replay_buffer_status()
{
	auto *result = obs_data_create();
	obs_data_set_bool(result, "replayBufferActive", obs_frontend_replay_buffer_active());
	return result;
}

obs_data_t *virtual_camera_status()
{
	auto *result = obs_data_create();
	obs_data_set_bool(result, "virtualCameraActive", obs_frontend_virtualcam_active());
	return result;
}

void append_output(obs_data_array_t *outputs, const char *name, const char *kind, bool active)
{
	if (obs_data_array_count(outputs) >= kMaxOutputs)
		return;
	auto *entry = obs_data_create();
	obs_data_set_string(entry, "outputName", name);
	obs_data_set_string(entry, "outputKind", kind);
	obs_data_set_bool(entry, "outputActive", active);
	obs_data_array_push_back(outputs, entry);
	obs_data_release(entry);
}

obs_data_t *list_outputs()
{
	auto *result = obs_data_create();
	auto *outputs = obs_data_array_create();
	append_output(outputs, "recording", "recording", obs_frontend_recording_active());
	append_output(outputs, "streaming", "streaming", obs_frontend_streaming_active());
	append_output(outputs, "replay_buffer", "replay_buffer", obs_frontend_replay_buffer_active());
	append_output(outputs, "virtual_camera", "virtual_camera", obs_frontend_virtualcam_active());
	obs_data_set_array(result, "outputs", outputs);
	obs_data_set_bool(result, "truncated", false);
	obs_data_array_release(outputs);
	return result;
}

obs_data_t *output_status(const std::string &name)
{
	auto *result = obs_data_create();
	bool active = false;
	const char *kind = nullptr;
	if (name == "recording") {
		active = obs_frontend_recording_active();
		kind = "recording";
	} else if (name == "streaming") {
		active = obs_frontend_streaming_active();
		kind = "streaming";
	} else if (name == "replay_buffer") {
		active = obs_frontend_replay_buffer_active();
		kind = "replay_buffer";
	} else if (name == "virtual_camera") {
		active = obs_frontend_virtualcam_active();
		kind = "virtual_camera";
	} else {
		set_error(result, "OBS_OUTPUT_NOT_FOUND");
		return result;
	}
	obs_data_set_string(result, "outputName", name.c_str());
	obs_data_set_string(result, "outputKind", kind);
	obs_data_set_bool(result, "outputActive", active);
	return result;
}

obs_data_t *capture_program_frame()
{
	obs_data_t *result = obs_data_create();
	obs_source_t *program = obs_frontend_get_current_scene();
	if (program == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	const char *source_name = obs_source_get_name(program);
	if (source_name == nullptr || !*source_name || std::strlen(source_name) > 256) {
		obs_source_release(program);
		set_error(result, "OBS_RESPONSE_INVALID");
		return result;
	}

	obs_data_t *screenshot_request = obs_data_create();
	obs_data_set_string(screenshot_request, "sourceName", source_name);
	obs_data_set_string(screenshot_request, "imageFormat", "png");
	obs_data_set_int(screenshot_request, "imageWidth", 320);
	obs_data_set_int(screenshot_request, "imageHeight", 180);
	auto *screenshot_response = obs_websocket_call_request("GetSourceScreenshot", screenshot_request);
	obs_data_release(screenshot_request);

	obs_data_t *screenshot_data = nullptr;
	if (screenshot_response != nullptr && screenshot_response->status_code == kObsWebSocketSuccessStatus &&
	    screenshot_response->response_data != nullptr &&
	    std::strlen(screenshot_response->response_data) <= kMaxProgramFrameDataUrlBytes + 1024)
		screenshot_data = obs_data_create_from_json(screenshot_response->response_data);
	const char *image_data = screenshot_data != nullptr ? obs_data_get_string(screenshot_data, "imageData")
							    : nullptr;
	constexpr char kPngDataUrlPrefix[] = "data:image/png;base64,";
	if (image_data == nullptr || std::strncmp(image_data, kPngDataUrlPrefix, sizeof(kPngDataUrlPrefix) - 1) != 0 ||
	    std::strlen(image_data) > kMaxProgramFrameDataUrlBytes) {
		set_error(result, "OBS_SCREENSHOT_UNVERIFIED");
	} else {
		obs_data_set_string(result, "sourceName", source_name);
		obs_data_set_string(result, "imageFormat", "png");
		obs_data_set_string(result, "imageData", image_data);
	}
	if (screenshot_data != nullptr)
		obs_data_release(screenshot_data);
	obs_websocket_request_response_free(screenshot_response);
	obs_source_release(program);
	return result;
}

void execute_ui_operation(void *private_data)
{
	std::unique_ptr<std::shared_ptr<UiState>> holder(static_cast<std::shared_ptr<UiState> *>(private_data));
	std::shared_ptr<UiState> state = *holder;
	obs_data_t *result = nullptr;
	const bool is_mutation =
		state->operation == UiOperation::CreateAgentInputOverlay ||
		state->operation == UiOperation::SetAgentInputOverlayLayout ||
		state->operation == UiOperation::EmitAgentInputActivity ||
		state->operation == UiOperation::ClearAgentInputOverlay ||
		state->operation == UiOperation::RequestGracefulShutdown ||
		state->operation == UiOperation::StartRecording || state->operation == UiOperation::StopRecording ||
		state->operation == UiOperation::PauseRecording || state->operation == UiOperation::ResumeRecording ||
		state->operation == UiOperation::StartSceneRecordings ||
		state->operation == UiOperation::StopSceneRecordings ||
		state->operation == UiOperation::StartStreaming || state->operation == UiOperation::StopStreaming ||
		state->operation == UiOperation::StartReplayBuffer ||
		state->operation == UiOperation::StopReplayBuffer ||
		state->operation == UiOperation::SaveReplayBuffer ||
		state->operation == UiOperation::StartVirtualCamera ||
		state->operation == UiOperation::StopVirtualCamera || state->operation == UiOperation::StartOutput ||
		state->operation == UiOperation::StopOutput || state->operation == UiOperation::SetProfile ||
		state->operation == UiOperation::SetSceneCollection ||
		state->operation == UiOperation::TriggerAllowlistedHotkey ||
		state->operation == UiOperation::CaptureScreenshot ||
		state->operation == UiOperation::SetCurrentScene || state->operation == UiOperation::CreateScene ||
		state->operation == UiOperation::RenameScene || state->operation == UiOperation::RemoveScene ||
		state->operation == UiOperation::CreateSceneItem ||
		state->operation == UiOperation::CreateWindowCaptureSource ||
		state->operation == UiOperation::RestoreWindowCaptureCandidate ||
		state->operation == UiOperation::RebindWindowCaptureSource ||
		state->operation == UiOperation::SetWindowCaptureMethod ||
		state->operation == UiOperation::SetSceneItemEnabled ||
		state->operation == UiOperation::SetSceneItemTransform ||
		state->operation == UiOperation::RemoveSceneItem ||
		state->operation == UiOperation::SetCurrentTransition ||
		state->operation == UiOperation::TriggerTransition || state->operation == UiOperation::SetStudioMode ||
		state->operation == UiOperation::SetCurrentPreviewScene ||
		state->operation == UiOperation::TriggerStudioModeTransition ||
		(state->operation == UiOperation::TypedSourceControl &&
		 dcc_mcp_obs::is_typed_source_mutation(state->typed_source_request.request_name));
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
		case UiOperation::OperatorStatus:
			result = obs_data_create();
			obs_data_set_bool(result, "ready", true);
			obs_data_set_bool(result, "uiThreadReady", true);
			// Never return profile paths, config paths, or credentials.
			obs_data_set_bool(result, "configPathRedacted", true);
			{
				char *profile = obs_frontend_get_current_profile();
				if (profile != nullptr) {
					obs_data_set_string(result, "profileName", profile);
					bfree(profile);
				}
				char *collection = obs_frontend_get_current_scene_collection();
				if (collection != nullptr) {
					obs_data_set_string(result, "sceneCollectionName", collection);
					bfree(collection);
				}
			}
			break;
		case UiOperation::ListScenes:
			result = list_scenes();
			break;
		case UiOperation::ListSources:
			result = list_sources(state->scene_name);
			break;
		case UiOperation::GetCurrentScene:
			result = current_scene_status();
			break;
		case UiOperation::SetCurrentScene: {
			result = obs_data_create();
			auto *scene = scene_source_by_name(state->scene_name);
			if (scene == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_set_current_scene(scene);
				auto *current = obs_frontend_get_current_scene();
				if (current == nullptr || state->scene_name != obs_source_get_name(current))
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_string(result, "sceneName", obs_source_get_name(current));
					obs_data_set_bool(result, "accepted", true);
				}
				if (current != nullptr)
					obs_source_release(current);
			}
			if (scene != nullptr)
				obs_source_release(scene);
			break;
		}
		case UiOperation::CreateScene: {
			result = obs_data_create();
			if (state->scene_name.empty())
				set_error(result, "OBS_ARGUMENT_INVALID");
			else {
				auto *existing = obs_get_source_by_name(state->scene_name.c_str());
				if (existing != nullptr) {
					obs_source_release(existing);
					set_error(result, "OBS_TARGET_AMBIGUOUS");
				}
			}
			if (obs_data_has_user_value(result, "ok"))
				break;
			if (state->scene_name.empty()) {
				set_error(result, "OBS_ARGUMENT_INVALID");
				break;
			}
			if (!state->gate.claim_mutation(state->deadline)) {
				set_error(result, "OBS_UI_TIMEOUT");
				break;
			}
			{
				auto *new_scene = obs_scene_create(state->scene_name.c_str());
				if (new_scene == nullptr)
					set_error(result, "OBS_MUTATION_REJECTED");
				else {
					auto *source = obs_scene_get_source(new_scene);
					if (source == nullptr || state->scene_name != obs_source_get_name(source))
						set_error(result, "OBS_POSTCONDITION_FAILED");
					else {
						obs_data_set_string(result, "sceneName", obs_source_get_name(source));
						obs_data_set_bool(result, "accepted", true);
					}
					obs_scene_release(new_scene);
				}
			}
			break;
		}
		case UiOperation::RenameScene: {
			result = obs_data_create();
			auto *scene = scene_source_by_name(state->scene_name);
			auto *conflict = state->target_name.empty()
						 ? nullptr
						 : obs_get_source_by_name(state->target_name.c_str());
			if (conflict != nullptr) {
				obs_source_release(conflict);
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			} else if (scene == nullptr || state->target_name.empty())
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_source_set_name(scene, state->target_name.c_str());
				auto *renamed = obs_get_source_by_name(state->target_name.c_str());
				if (renamed == nullptr || state->target_name != obs_source_get_name(renamed))
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_string(result, "sceneName", obs_source_get_name(renamed));
					obs_data_set_bool(result, "accepted", true);
				}
				if (renamed != nullptr)
					obs_source_release(renamed);
			}
			if (scene != nullptr)
				obs_source_release(scene);
			break;
		}
		case UiOperation::RemoveScene: {
			result = obs_data_create();
			auto *scene = scene_source_by_name(state->scene_name);
			if (scene == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_source_remove(scene);
				auto *remaining = obs_get_source_by_name(state->scene_name.c_str());
				if (remaining != nullptr) {
					obs_source_release(remaining);
					set_error(result, "OBS_POSTCONDITION_FAILED");
				} else {
					obs_data_set_string(result, "sceneName", state->scene_name.c_str());
					obs_data_set_bool(result, "accepted", true);
				}
			}
			if (scene != nullptr)
				obs_source_release(scene);
			break;
		}
		case UiOperation::ListSceneItems:
			result = list_scene_items(state->scene_name);
			break;
		case UiOperation::GetSceneItem:
			result = scene_item_status(state->scene_name, state->scene_item_id);
			break;
		case UiOperation::CreateSceneItem: {
			result = obs_data_create();
			auto *scene_source = scene_source_by_name(state->scene_name);
			auto *source = state->source_name.empty() ? nullptr
								  : obs_get_source_by_name(state->source_name.c_str());
			if (scene_source == nullptr || source == nullptr)
				set_error(result,
					  scene_source == nullptr ? "OBS_SCENE_NOT_FOUND" : "OBS_SOURCE_NOT_FOUND");
			else if (!state->source_kind.empty() &&
				 std::string(obs_source_get_id(source)) != state->source_kind)
				set_error(result, "OBS_ARGUMENT_INVALID");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				auto *scene = obs_scene_from_source(scene_source);
				auto *item = scene == nullptr ? nullptr : obs_scene_add(scene, source);
				if (item == nullptr)
					set_error(result, "OBS_MUTATION_REJECTED");
				else {
					obs_sceneitem_set_visible(item, state->enabled);
					set_scene_item_data(result, state->scene_name, item);
					obs_data_set_bool(result, "accepted", true);
				}
			}
			if (source != nullptr)
				obs_source_release(source);
			if (scene_source != nullptr)
				obs_source_release(scene_source);
			break;
		}
		case UiOperation::ListWindowCaptureCandidates:
#ifndef _WIN32
			result = obs_data_create();
			set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
			result = list_window_capture_candidates(state->window_executable_filter,
								state->window_title_filter);
#endif
			break;
		case UiOperation::RestoreWindowCaptureCandidate: {
			result = obs_data_create();
#ifndef _WIN32
			set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
			ValidatedWindowBinding actual;
			if (const char *error = validate_exact_window(state->window_capture, actual);
			    error != nullptr) {
				set_error(result, error);
			} else if (_stricmp(actual.executable.c_str(), state->window_executable_filter.c_str()) != 0) {
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			} else if (!state->gate.claim_mutation(state->deadline)) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else if (!exact_window_is_still_live(state->window_capture, actual)) {
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			} else {
				HWND window = reinterpret_cast<HWND>(
					static_cast<uintptr_t>(state->window_capture.window_handle));
				if (IsIconic(window) && !ShowWindowAsync(window, SW_RESTORE)) {
					set_error(result, "OBS_MUTATION_REJECTED");
				} else {
					set_window_capture_candidate_data(result, window,
									  state->window_capture.process_id,
									  actual.title, actual.window_class,
									  actual.executable);
					obs_data_set_bool(result, "accepted", true);
					obs_data_set_string(result, "capability", "window_capture");
				}
			}
#endif
			break;
		}
		case UiOperation::GetWindowCaptureSource:
			result = window_capture_source_status(*state);
			break;
		case UiOperation::RebindWindowCaptureSource: {
			result = obs_data_create();
#ifndef _WIN32
			set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
			ValidatedWindowBinding actual;
			if (const char *error = validate_exact_window(state->window_capture, actual);
			    error != nullptr) {
				set_error(result, error);
				break;
			}
			auto *scene_source = scene_source_by_name(state->scene_name);
			auto *source = state->source_name.empty() ? nullptr
								  : obs_get_source_by_name(state->source_name.c_str());
			auto *scene = scene_source == nullptr ? nullptr : obs_scene_from_source(scene_source);
			SceneSourceMatch match{state->source_name};
			if (scene != nullptr)
				obs_scene_enum_items(scene, find_scene_source_item, &match);
			auto *settings = source == nullptr ? nullptr : obs_source_get_settings(source);
			const bool source_owned =
				source != nullptr && std::string(obs_source_get_id(source)) == "window_capture" &&
				match.count == 1 && match.item != nullptr &&
				stored_window_capture_identity_matches(settings, state->expected_window_capture) &&
				stored_window_capture_configuration_matches(settings, *state) &&
				obs_sceneitem_visible(match.item) == state->enabled;
			if (scene_source == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (source == nullptr)
				set_error(result, "OBS_SOURCE_NOT_FOUND");
			else if (match.count > 1)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (!source_owned)
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!exact_window_is_still_live(state->window_capture, actual))
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			else {
				auto *previous_settings = obs_data_create();
				obs_data_apply(previous_settings, settings);
				obs_data_set_string(settings, "window", actual.selector.c_str());
				obs_data_set_int(settings, "_dcc_process_id", state->window_capture.process_id);
				obs_data_set_int(settings, "_dcc_window_handle",
						 static_cast<long long>(state->window_capture.window_handle));
				obs_data_set_int(settings, "_dcc_process_created",
						 static_cast<long long>(actual.process_created));
				obs_data_set_string(settings, "_dcc_window_title", actual.title.c_str());
				obs_data_set_string(settings, "_dcc_window_class", actual.window_class.c_str());
				obs_data_set_string(settings, "_dcc_executable", actual.executable.c_str());
				obs_source_update(source, settings);
				auto *verified = window_capture_source_status(*state);
				if (obs_data_has_user_value(verified, "ok")) {
					obs_data_apply(settings, previous_settings);
					obs_source_update(source, previous_settings);
				}
				obs_data_release(previous_settings);
				obs_data_release(result);
				result = verified;
				if (!obs_data_has_user_value(result, "ok")) {
					obs_data_set_bool(result, "accepted", true);
					obs_data_set_string(result, "capability", "window_capture");
				}
			}
			if (settings != nullptr)
				obs_data_release(settings);
			if (source != nullptr)
				obs_source_release(source);
			if (scene_source != nullptr)
				obs_source_release(scene_source);
#endif
			break;
		}
		case UiOperation::CreateWindowCaptureSource: {
			result = obs_data_create();
#ifndef _WIN32
			set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
			ValidatedWindowBinding actual;
			if (const char *error = validate_exact_window(state->window_capture, actual);
			    error != nullptr) {
				set_error(result, error);
				break;
			}
			auto *scene_source = scene_source_by_name(state->scene_name);
			auto *existing = state->source_name.empty()
						 ? nullptr
						 : obs_get_source_by_name(state->source_name.c_str());
			if (scene_source == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (existing != nullptr)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!exact_window_is_still_live(state->window_capture, actual))
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			else {
				auto *settings = obs_data_create();
				obs_data_set_string(settings, "window", actual.selector.c_str());
				obs_data_set_int(settings, "method",
						 window_capture_method_value(state->window_capture.capture_method));
				obs_data_set_int(settings, "priority", 1);
				obs_data_set_bool(settings, "cursor", state->window_capture.capture_cursor);
				obs_data_set_bool(settings, "client_area", state->window_capture.client_area);
				obs_data_set_bool(settings, "capture_audio", false);
				obs_data_set_string(settings, "_dcc_binding_schema", kWindowBindingSchema);
				obs_data_set_int(settings, "_dcc_process_id", state->window_capture.process_id);
				obs_data_set_int(settings, "_dcc_window_handle",
						 static_cast<long long>(state->window_capture.window_handle));
				obs_data_set_int(settings, "_dcc_process_created",
						 static_cast<long long>(actual.process_created));
				obs_data_set_string(settings, "_dcc_window_title", actual.title.c_str());
				obs_data_set_string(settings, "_dcc_window_class", actual.window_class.c_str());
				obs_data_set_string(settings, "_dcc_executable", actual.executable.c_str());
				auto *source = obs_source_create("window_capture", state->source_name.c_str(), settings,
								 nullptr);
				obs_data_release(settings);
				auto *scene = obs_scene_from_source(scene_source);
				auto *item = source == nullptr || scene == nullptr ? nullptr
										   : obs_scene_add(scene, source);
				if (item == nullptr)
					set_error(result, "OBS_MUTATION_REJECTED");
				else {
					obs_sceneitem_set_visible(item, state->enabled);
					auto *verified = window_capture_source_status(*state);
					if (obs_data_has_user_value(verified, "ok")) {
						obs_sceneitem_remove(item);
						obs_source_remove(source);
					}
					obs_data_release(result);
					result = verified;
					if (!obs_data_has_user_value(result, "ok")) {
						obs_data_set_bool(result, "accepted", true);
						obs_data_set_string(result, "capability", "window_capture");
					}
				}
				if (source != nullptr)
					obs_source_release(source);
			}
			if (existing != nullptr)
				obs_source_release(existing);
			if (scene_source != nullptr)
				obs_source_release(scene_source);
#endif
			break;
		}
		case UiOperation::SetWindowCaptureMethod: {
			result = obs_data_create();
#ifndef _WIN32
			set_error(result, "OBS_UNSUPPORTED_PLATFORM");
#else
			ValidatedWindowBinding actual;
			if (const char *error = validate_exact_window(state->window_capture, actual);
			    error != nullptr) {
				set_error(result, error);
				break;
			}
			auto *scene_source = scene_source_by_name(state->scene_name);
			auto *source = state->source_name.empty() ? nullptr
								  : obs_get_source_by_name(state->source_name.c_str());
			auto *scene = scene_source == nullptr ? nullptr : obs_scene_from_source(scene_source);
			SceneSourceMatch match{state->source_name};
			if (scene != nullptr)
				obs_scene_enum_items(scene, find_scene_source_item, &match);
			auto *settings = source == nullptr ? nullptr : obs_source_get_settings(source);
			const bool source_matches = source != nullptr &&
						    std::string(obs_source_get_id(source)) == "window_capture" &&
						    match.count == 1 && match.item != nullptr &&
						    window_capture_settings_match(settings, *state, actual, false) &&
						    obs_sceneitem_visible(match.item) == state->enabled &&
						    exact_window_is_still_live(state->window_capture, actual);
			if (scene_source == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (source == nullptr)
				set_error(result, "OBS_SOURCE_NOT_FOUND");
			else if (match.count > 1)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (!source_matches)
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
			else {
				const long long previous_method = obs_data_get_int(settings, "method");
				const int requested_method =
					window_capture_method_value(state->window_capture.capture_method);
				if (previous_method != requested_method &&
				    !state->gate.claim_mutation(state->deadline)) {
					set_error(result, "OBS_UI_TIMEOUT");
				} else if (!exact_window_is_still_live(state->window_capture, actual)) {
					set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
				} else {
					if (previous_method != requested_method) {
						obs_data_set_int(settings, "method", requested_method);
						obs_source_update(source, settings);
					}
					auto *verified = window_capture_source_status(*state);
					if (obs_data_has_user_value(verified, "ok") &&
					    previous_method != requested_method) {
						obs_data_set_int(settings, "method", previous_method);
						obs_source_update(source, settings);
					}
					obs_data_release(result);
					result = verified;
					if (!obs_data_has_user_value(result, "ok")) {
						obs_data_set_bool(result, "accepted", true);
						obs_data_set_string(result, "capability", "window_capture");
					}
				}
			}
			if (settings != nullptr)
				obs_data_release(settings);
			if (source != nullptr)
				obs_source_release(source);
			if (scene_source != nullptr)
				obs_source_release(scene_source);
#endif
			break;
		}
		case UiOperation::SetSceneItemEnabled:
		case UiOperation::SetSceneItemTransform:
		case UiOperation::RemoveSceneItem: {
			result = obs_data_create();
			auto *scene_source = scene_source_by_name(state->scene_name);
			auto *scene = scene_source == nullptr ? nullptr : obs_scene_from_source(scene_source);
			auto *item = scene == nullptr ? nullptr
						      : obs_scene_find_sceneitem_by_id(scene, state->scene_item_id);
			if (item == nullptr)
				set_error(result,
					  scene_source == nullptr ? "OBS_SCENE_NOT_FOUND" : "OBS_SCENE_ITEM_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (state->operation == UiOperation::RemoveSceneItem) {
				obs_sceneitem_remove(item);
				if (obs_scene_find_sceneitem_by_id(scene, state->scene_item_id) != nullptr)
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_string(result, "sceneName", state->scene_name.c_str());
					obs_data_set_int(result, "sceneItemId", state->scene_item_id);
					obs_data_set_bool(result, "accepted", true);
				}
			} else {
				if (state->operation == UiOperation::SetSceneItemEnabled)
					obs_sceneitem_set_visible(item, state->enabled);
				else {
					vec2 pos{}, scale{};
					obs_sceneitem_get_pos(item, &pos);
					obs_sceneitem_get_scale(item, &scale);
					if (state->has_pos) {
						pos.x = state->pos_x;
						pos.y = state->pos_y;
						obs_sceneitem_set_pos(item, &pos);
					}
					if (state->has_scale) {
						scale.x = state->scale_x;
						scale.y = state->scale_y;
						obs_sceneitem_set_scale(item, &scale);
					}
					if (state->has_rotation)
						obs_sceneitem_set_rot(item, state->rotation);
				}
				bool verified = true;
				if (state->operation == UiOperation::SetSceneItemEnabled)
					verified = obs_sceneitem_visible(item) == state->enabled;
				if (state->operation == UiOperation::SetSceneItemTransform) {
					vec2 actual_pos{}, actual_scale{};
					obs_sceneitem_get_pos(item, &actual_pos);
					obs_sceneitem_get_scale(item, &actual_scale);
					if (state->has_pos)
						verified = verified &&
							   std::fabs(actual_pos.x - state->pos_x) < 0.001f &&
							   std::fabs(actual_pos.y - state->pos_y) < 0.001f;
					if (state->has_scale)
						verified = verified &&
							   std::fabs(actual_scale.x - state->scale_x) < 0.001f &&
							   std::fabs(actual_scale.y - state->scale_y) < 0.001f;
					if (state->has_rotation)
						verified = verified && std::fabs(obs_sceneitem_get_rot(item) -
										 state->rotation) < 0.001f;
				}
				if (!verified)
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					set_scene_item_data(result, state->scene_name, item);
					obs_data_set_bool(result, "accepted", true);
				}
			}
			if (scene_source != nullptr)
				obs_source_release(scene_source);
			break;
		}
		case UiOperation::ListTransitions:
			result = list_transitions();
			break;
		case UiOperation::GetCurrentTransition: {
			result = obs_data_create();
			auto *transition = obs_frontend_get_current_transition();
			if (transition == nullptr)
				set_error(result, "OBS_TRANSITION_NOT_FOUND");
			else {
				obs_data_set_string(result, "transitionName", obs_source_get_name(transition));
				obs_data_set_int(result, "durationMs", obs_frontend_get_transition_duration());
				obs_source_release(transition);
			}
			break;
		}
		case UiOperation::SetCurrentTransition: {
			result = obs_data_create();
			const FrontendTransitionLookup lookup = lookup_frontend_transition(state->transition_name);
			auto *transition = lookup.source;
			if (lookup.status == NameLookup::Incomplete)
				set_error(result, "OBS_RESPONSE_INCOMPLETE");
			else if (lookup.status == NameLookup::Ambiguous)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (lookup.status != NameLookup::Unique || transition == nullptr)
				set_error(result, "OBS_TRANSITION_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_set_current_transition(transition);
				if (state->has_duration)
					obs_frontend_set_transition_duration(state->duration_ms);
				obs_data_set_string(result, "transitionName", state->transition_name.c_str());
				obs_data_set_int(result, "durationMs",
						 state->has_duration ? state->duration_ms
								     : obs_frontend_get_transition_duration());
				obs_data_set_bool(result, "accepted", true);
			}
			if (transition)
				obs_source_release(transition);
			break;
		}
		case UiOperation::TriggerTransition: {
			result = obs_data_create();
			auto *transition = obs_frontend_get_current_transition();
			auto *destination = scene_source_by_name(state->scene_name);
			if (transition == nullptr || destination == nullptr)
				set_error(result, "OBS_TRANSITION_NOT_FOUND");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_set_current_scene(destination);
				auto *current = obs_frontend_get_current_scene();
				if (current == nullptr || state->scene_name != obs_source_get_name(current))
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_string(result, "sceneName", obs_source_get_name(current));
					obs_data_set_bool(result, "accepted", true);
				}
				if (current)
					obs_source_release(current);
			}
			if (transition)
				obs_source_release(transition);
			if (destination)
				obs_source_release(destination);
			break;
		}
		case UiOperation::GetStudioModeStatus:
			result = studio_mode_status();
			break;
		case UiOperation::SetStudioMode: {
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_set_preview_program_mode(state->studio_enabled);
				if (obs_frontend_preview_program_mode_active() != state->studio_enabled)
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_bool(result, "studioModeEnabled", state->studio_enabled);
					obs_data_set_bool(result, "accepted", true);
				}
			}
			break;
		}
		case UiOperation::GetCurrentPreviewScene: {
			result = obs_data_create();
			auto *preview = obs_frontend_get_current_preview_scene();
			if (preview == nullptr)
				set_error(result, "OBS_STUDIO_MODE_INACTIVE");
			else {
				obs_data_set_string(result, "sceneName", obs_source_get_name(preview));
				obs_source_release(preview);
			}
			break;
		}
		case UiOperation::SetCurrentPreviewScene: {
			result = obs_data_create();
			auto *scene = scene_source_by_name(state->scene_name);
			if (scene == nullptr)
				set_error(result, "OBS_SCENE_NOT_FOUND");
			else if (!obs_frontend_preview_program_mode_active())
				set_error(result, "OBS_STUDIO_MODE_INACTIVE");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_set_current_preview_scene(scene);
				auto *preview = obs_frontend_get_current_preview_scene();
				if (preview == nullptr || state->scene_name != obs_source_get_name(preview))
					set_error(result, "OBS_POSTCONDITION_FAILED");
				else {
					obs_data_set_string(result, "sceneName", obs_source_get_name(preview));
					obs_data_set_bool(result, "accepted", true);
				}
				if (preview)
					obs_source_release(preview);
			}
			if (scene != nullptr)
				obs_source_release(scene);
			break;
		}
		case UiOperation::TriggerStudioModeTransition: {
			result = obs_data_create();
			if (!obs_frontend_preview_program_mode_active())
				set_error(result, "OBS_STUDIO_MODE_INACTIVE");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_frontend_preview_program_trigger_transition();
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::CreateAgentInputOverlay:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				result = dcc_mcp_obs::create_agent_input_overlay(state->scene_name, state->source_name,
										 state->overlay_anchor);
			}
			break;
		case UiOperation::GetAgentInputOverlay:
			result = dcc_mcp_obs::get_agent_input_overlay(state->scene_name, state->source_name);
			break;
		case UiOperation::SetAgentInputOverlayLayout:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				dcc_mcp_obs::AgentInputOverlayLayout layout;
				layout.anchor = state->overlay_anchor;
				layout.opacity = state->overlay_opacity;
				layout.margin = state->overlay_margin;
				result = dcc_mcp_obs::set_agent_input_overlay_layout(state->scene_name,
										     state->source_name, layout);
			}
			break;
		case UiOperation::EmitAgentInputActivity:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				result = dcc_mcp_obs::emit_agent_input_activity(state->scene_name, state->source_name,
										state->agent_input_activity);
			}
			break;
		case UiOperation::ClearAgentInputOverlay:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				result = dcc_mcp_obs::clear_agent_input_overlay(state->scene_name, state->source_name);
			}
			break;
		case UiOperation::RequestGracefulShutdown: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			const bool streaming_active = obs_frontend_streaming_active();
			const bool replay_buffer_active = obs_frontend_replay_buffer_active();
			const bool virtual_camera_active = obs_frontend_virtualcam_active();
			const bool scene_recording_active = g_scene_recording_sessions != nullptr &&
							    g_scene_recording_sessions->active();
			const void *main_window = obs_frontend_get_main_window();
			if (main_window == nullptr)
				set_error(result, "OBS_INSTANCE_NOT_READY");
			else if (recording_active || streaming_active || replay_buffer_active ||
				 virtual_camera_active || scene_recording_active)
				set_error(result, "OBS_OUTPUT_ACTIVE");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				obs_data_set_bool(result, "accepted", true);
				obs_data_set_bool(result, "shutdownScheduled", true);
			}
			break;
		}
		case UiOperation::RecordingStatus:
			result = recording_status();
			break;
		case UiOperation::StartRecording: {
			result = obs_data_create();
			const bool recording_active = obs_frontend_recording_active();
			if (!state->gate.claim_mutation(state->deadline)) {
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
			if (!state->gate.claim_mutation(state->deadline)) {
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
			if (!state->gate.claim_mutation(state->deadline)) {
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
			if (!state->gate.claim_mutation(state->deadline)) {
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
		case UiOperation::StartSceneRecordings:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else if (g_scene_recording_sessions == nullptr) {
				result = obs_data_create();
				set_error(result, "OBS_INSTANCE_NOT_READY");
			} else {
				result = g_scene_recording_sessions->start(state->scene_recording_specs);
			}
			break;
		case UiOperation::GetSceneRecordingSession:
			result = g_scene_recording_sessions != nullptr
					 ? g_scene_recording_sessions->status(state->scene_recording_session_id)
					 : obs_data_create();
			if (g_scene_recording_sessions == nullptr)
				set_error(result, "OBS_INSTANCE_NOT_READY");
			break;
		case UiOperation::StopSceneRecordings:
			if (!state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else if (g_scene_recording_sessions == nullptr) {
				result = obs_data_create();
				set_error(result, "OBS_INSTANCE_NOT_READY");
			} else {
				result = g_scene_recording_sessions->stop(state->scene_recording_session_id);
			}
			break;
		case UiOperation::StreamingStatus:
			result = streaming_status();
			break;
		case UiOperation::StartStreaming:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!obs_frontend_streaming_active())
				obs_frontend_streaming_start();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::StopStreaming:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (obs_frontend_streaming_active())
				obs_frontend_streaming_stop();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::ReplayBufferStatus:
			result = replay_buffer_status();
			break;
		case UiOperation::StartReplayBuffer:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!obs_frontend_replay_buffer_active())
				obs_frontend_replay_buffer_start();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::StopReplayBuffer:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (obs_frontend_replay_buffer_active())
				obs_frontend_replay_buffer_stop();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::SaveReplayBuffer:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!obs_frontend_replay_buffer_active())
				set_error(result, "OBS_OUTPUT_NOT_ACTIVE");
			else {
				obs_frontend_replay_buffer_save();
				obs_data_set_bool(result, "accepted", true);
				obs_data_set_bool(result, "submitted", true);
			}
			break;
		case UiOperation::VirtualCameraStatus:
			result = virtual_camera_status();
			break;
		case UiOperation::StartVirtualCamera:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (!obs_frontend_virtualcam_active())
				obs_frontend_start_virtualcam();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::StopVirtualCamera:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else if (obs_frontend_virtualcam_active())
				obs_frontend_stop_virtualcam();
			if (!obs_data_has_user_value(result, "ok"))
				obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::ListOutputs:
			result = list_outputs();
			break;
		case UiOperation::OutputStatus:
			result = output_status(state->output_name);
			break;
		case UiOperation::StartOutput:
		case UiOperation::StopOutput:
			result = obs_data_create();
			if (!state->gate.claim_mutation(state->deadline)) {
				set_error(result, "OBS_UI_TIMEOUT");
				break;
			}
			if (state->output_name == "recording") {
				if (state->operation == UiOperation::StartOutput)
					obs_frontend_recording_start();
				else
					obs_frontend_recording_stop();
			} else if (state->output_name == "streaming") {
				if (state->operation == UiOperation::StartOutput)
					obs_frontend_streaming_start();
				else
					obs_frontend_streaming_stop();
			} else if (state->output_name == "replay_buffer") {
				if (state->operation == UiOperation::StartOutput)
					obs_frontend_replay_buffer_start();
				else
					obs_frontend_replay_buffer_stop();
			} else if (state->output_name == "virtual_camera") {
				if (state->operation == UiOperation::StartOutput)
					obs_frontend_start_virtualcam();
				else
					obs_frontend_stop_virtualcam();
			} else {
				set_error(result, "OBS_OUTPUT_NOT_FOUND");
				break;
			}
			obs_data_set_bool(result, "accepted", true);
			break;
		case UiOperation::ListProfiles:
			result = list_profiles();
			break;
		case UiOperation::ProfileStatus:
			result = profile_status();
			break;
		case UiOperation::SetProfile: {
			result = obs_data_create();
			char **profiles = obs_frontend_get_profiles();
			const NameLookup lookup = lookup_name(profiles, state->target_name, kMaxProfiles);
			if (profiles != nullptr)
				bfree(profiles);
			if (lookup == NameLookup::Incomplete)
				set_error(result, "OBS_RESPONSE_INCOMPLETE");
			else if (lookup == NameLookup::Ambiguous)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (lookup != NameLookup::Unique) {
				set_error(result, "OBS_PROFILE_NOT_FOUND");
			} else if (!state->gate.claim_mutation(state->deadline)) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				obs_frontend_set_current_profile(state->target_name.c_str());
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::ListSceneCollections:
			result = list_scene_collections();
			break;
		case UiOperation::SceneCollectionStatus:
			result = scene_collection_status();
			break;
		case UiOperation::SetSceneCollection: {
			result = obs_data_create();
			char **collections = obs_frontend_get_scene_collections();
			const NameLookup lookup = lookup_name(collections, state->target_name, kMaxSceneCollections);
			if (collections != nullptr)
				bfree(collections);
			if (lookup == NameLookup::Incomplete)
				set_error(result, "OBS_RESPONSE_INCOMPLETE");
			else if (lookup == NameLookup::Ambiguous)
				set_error(result, "OBS_TARGET_AMBIGUOUS");
			else if (lookup != NameLookup::Unique) {
				set_error(result, "OBS_SCENE_COLLECTION_NOT_FOUND");
			} else if (!state->gate.claim_mutation(state->deadline)) {
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				obs_frontend_set_current_scene_collection(state->target_name.c_str());
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		}
		case UiOperation::ListAllowlistedHotkeys:
			result = list_allowlisted_hotkeys();
			break;
		case UiOperation::TriggerAllowlistedHotkey:
			result = obs_data_create();
			if (!allowlisted_hotkey(state->hotkey_name))
				set_error(result, "OBS_HOTKEY_NOT_ALLOWLISTED");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else {
				// Named hotkeys are mapped to typed frontend operations.  No raw
				// OBS hotkey id or key sequence crosses the vendor boundary.
				if (state->hotkey_name == "start_recording" ||
				    state->hotkey_name == "OBSBasic.StartRecording")
					obs_frontend_recording_start();
				else if (state->hotkey_name == "stop_recording" ||
					 state->hotkey_name == "OBSBasic.StopRecording")
					obs_frontend_recording_stop();
				else if (state->hotkey_name == "start_streaming" ||
					 state->hotkey_name == "OBSBasic.StartStreaming")
					obs_frontend_streaming_start();
				else if (state->hotkey_name == "stop_streaming" ||
					 state->hotkey_name == "OBSBasic.StopStreaming")
					obs_frontend_streaming_stop();
				else if (state->hotkey_name == "start_replay_buffer" ||
					 state->hotkey_name == "OBSBasic.StartReplayBuffer")
					obs_frontend_replay_buffer_start();
				else if (state->hotkey_name == "stop_replay_buffer" ||
					 state->hotkey_name == "OBSBasic.StopReplayBuffer")
					obs_frontend_replay_buffer_stop();
				else if (state->hotkey_name == "start_virtual_camera" ||
					 state->hotkey_name == "OBSBasic.StartVirtualCam")
					obs_frontend_start_virtualcam();
				else if (state->hotkey_name == "stop_virtual_camera" ||
					 state->hotkey_name == "OBSBasic.StopVirtualCam")
					obs_frontend_stop_virtualcam();
				obs_data_set_string(result, "hotkeyName", state->hotkey_name.c_str());
				obs_data_set_bool(result, "accepted", true);
			}
			break;
		case UiOperation::CaptureScreenshot: {
			result = obs_data_create();
			obs_source_t *screenshot_source = state->target_name.empty()
								  ? nullptr
								  : obs_get_source_by_name(state->target_name.c_str());
			if (screenshot_source == nullptr)
				set_error(result, "OBS_SCREENSHOT_INVALID");
			else if (!state->gate.claim_mutation(state->deadline))
				set_error(result, "OBS_UI_TIMEOUT");
			else
				// OBS exposes this API as fire-and-forget: there is no completion,
				// artifact identity, or safe path readback to return to MCP.
				set_error(result, "OBS_SCREENSHOT_UNVERIFIED");
			if (screenshot_source != nullptr)
				obs_source_release(screenshot_source);
			break;
		}
		case UiOperation::CaptureProgramFrame:
			result = capture_program_frame();
			break;
		case UiOperation::TypedSourceControl:
			if (is_mutation && !state->gate.claim_mutation(state->deadline)) {
				result = obs_data_create();
				set_error(result, "OBS_UI_TIMEOUT");
			} else {
				result = dcc_mcp_obs::execute_typed_source_request(state->typed_source_request);
			}
			break;
		case UiOperation::Invalid:
			result = obs_data_create();
			set_error(result, "OBS_REQUEST_INVALID");
			break;
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

bool run_ui_operation(UiOperation operation, const std::string &scene_name, const std::string &output_name,
		      const std::string &target_name, const std::string &hotkey_name, const std::string &image_format,
		      const std::string &source_name, const std::string &source_kind,
		      const std::string &transition_name, const std::string &window_executable_filter,
		      const std::string &window_title_filter, int64_t scene_item_id, bool enabled, bool studio_enabled,
		      bool has_duration, int duration_ms, bool has_pos, bool has_scale, bool has_rotation, float pos_x,
		      float pos_y, float scale_x, float scale_y, float rotation,
		      const WindowCaptureBinding &window_capture, const WindowCaptureBinding &expected_window_capture,
		      const std::string &overlay_anchor, int overlay_opacity, int overlay_margin,
		      const dcc_mcp_obs::AgentInputActivity &agent_input_activity,
		      const std::vector<dcc_mcp_obs::SceneRecordingSpec> &scene_recording_specs,
		      const std::string &scene_recording_session_id,
		      const dcc_mcp_obs::TypedSourceRequest &typed_source_request, uint64_t deadline_at_ms,
		      obs_data_t *response)
{
	auto state = std::make_shared<UiState>();
	state->operation = operation;
	state->scene_name = scene_name;
	state->output_name = output_name;
	state->target_name = target_name;
	state->hotkey_name = hotkey_name;
	state->image_format = image_format;
	state->source_name = source_name;
	state->source_kind = source_kind;
	state->transition_name = transition_name;
	state->overlay_anchor = overlay_anchor;
	state->overlay_opacity = overlay_opacity;
	state->overlay_margin = overlay_margin;
	state->agent_input_activity = agent_input_activity;
	state->scene_recording_specs = scene_recording_specs;
	state->scene_recording_session_id = scene_recording_session_id;
	state->typed_source_request = typed_source_request;
	state->window_executable_filter = window_executable_filter;
	state->window_title_filter = window_title_filter;
	state->window_capture = window_capture;
	state->expected_window_capture = expected_window_capture;
	state->scene_item_id = scene_item_id;
	state->enabled = enabled;
	state->studio_enabled = studio_enabled;
	state->has_duration = has_duration;
	state->duration_ms = duration_ms;
	state->has_pos = has_pos;
	state->has_scale = has_scale;
	state->has_rotation = has_rotation;
	state->pos_x = pos_x;
	state->pos_y = pos_y;
	state->scale_x = scale_x;
	state->scale_y = scale_y;
	state->rotation = rotation;
	const auto steady_now = std::chrono::steady_clock::now();
	const auto system_now = std::chrono::system_clock::now();
	state->deadline = deadline_at_ms == 0
				  ? steady_now + kUiTimeout
				  : dcc_mcp_obs::steady_deadline_from_epoch_ms(deadline_at_ms, steady_now, system_now);
	auto *holder = new std::shared_ptr<UiState>(state);
	obs_queue_task(OBS_TASK_UI, execute_ui_operation, holder, false);

	std::unique_lock<std::mutex> lock(state->mutex);
	auto remaining = state->deadline - std::chrono::steady_clock::now();
	if (remaining <= std::chrono::steady_clock::duration::zero()) {
		if (state->gate.cancel_pending()) {
			set_error(response, "OBS_UI_TIMEOUT");
			return false;
		}
		set_error(response, "OBS_UI_INDETERMINATE");
		obs_data_set_bool(response, "indeterminate", true);
		return false;
	}
	if (!state->condition.wait_for(
		    lock,
		    std::min(std::chrono::duration_cast<std::chrono::steady_clock::duration>(kUiTimeout), remaining),
		    [&state] { return state->complete; })) {
		if (state->gate.cancel_pending()) {
			set_error(response, "OBS_UI_TIMEOUT");
			return false;
		}
		set_error(response, "OBS_UI_INDETERMINATE");
		obs_data_set_bool(response, "indeterminate", true);
		return false;
	}
	obs_data_apply(response, state->result);
	return obs_data_get_bool(response, "ok");
}

UiOperation operation_for(const std::string &request)
{
	if (dcc_mcp_obs::is_typed_source_request(request))
		return UiOperation::TypedSourceControl;
	if (request == "GetPluginStatus")
		return UiOperation::Status;
	if (request == "GetOperatorStatus")
		return UiOperation::OperatorStatus;
	if (request == "ListScenes")
		return UiOperation::ListScenes;
	if (request == "SetCurrentScene")
		return UiOperation::SetCurrentScene;
	if (request == "GetCurrentScene")
		return UiOperation::GetCurrentScene;
	if (request == "CreateScene")
		return UiOperation::CreateScene;
	if (request == "RenameScene")
		return UiOperation::RenameScene;
	if (request == "RemoveScene")
		return UiOperation::RemoveScene;
	if (request == "ListSceneItems")
		return UiOperation::ListSceneItems;
	if (request == "GetSceneItem")
		return UiOperation::GetSceneItem;
	if (request == "CreateSceneItem")
		return UiOperation::CreateSceneItem;
	if (request == "ListWindowCaptureCandidates")
		return UiOperation::ListWindowCaptureCandidates;
	if (request == "RestoreWindowCaptureCandidate")
		return UiOperation::RestoreWindowCaptureCandidate;
	if (request == "CreateWindowCaptureSource")
		return UiOperation::CreateWindowCaptureSource;
	if (request == "GetWindowCaptureSource")
		return UiOperation::GetWindowCaptureSource;
	if (request == "RebindWindowCaptureSource")
		return UiOperation::RebindWindowCaptureSource;
	if (request == "SetWindowCaptureMethod")
		return UiOperation::SetWindowCaptureMethod;
	if (request == "SetSceneItemEnabled")
		return UiOperation::SetSceneItemEnabled;
	if (request == "SetSceneItemTransform")
		return UiOperation::SetSceneItemTransform;
	if (request == "RemoveSceneItem")
		return UiOperation::RemoveSceneItem;
	if (request == "ListTransitions")
		return UiOperation::ListTransitions;
	if (request == "GetCurrentTransition")
		return UiOperation::GetCurrentTransition;
	if (request == "SetCurrentTransition")
		return UiOperation::SetCurrentTransition;
	if (request == "TriggerTransition")
		return UiOperation::TriggerTransition;
	if (request == "GetStudioModeStatus")
		return UiOperation::GetStudioModeStatus;
	if (request == "SetStudioMode")
		return UiOperation::SetStudioMode;
	if (request == "GetCurrentPreviewScene")
		return UiOperation::GetCurrentPreviewScene;
	if (request == "SetCurrentPreviewScene")
		return UiOperation::SetCurrentPreviewScene;
	if (request == "TriggerStudioModeTransition")
		return UiOperation::TriggerStudioModeTransition;
	if (request == "ListSources")
		return UiOperation::ListSources;
	if (request == "CreateAgentInputOverlay")
		return UiOperation::CreateAgentInputOverlay;
	if (request == "GetAgentInputOverlay")
		return UiOperation::GetAgentInputOverlay;
	if (request == "SetAgentInputOverlayLayout")
		return UiOperation::SetAgentInputOverlayLayout;
	if (request == "EmitAgentInputActivity")
		return UiOperation::EmitAgentInputActivity;
	if (request == "ClearAgentInputOverlay")
		return UiOperation::ClearAgentInputOverlay;
	if (request == "RequestGracefulShutdown")
		return UiOperation::RequestGracefulShutdown;
	if (request == "GetRecordingStatus")
		return UiOperation::RecordingStatus;
	if (request == "StartRecording")
		return UiOperation::StartRecording;
	if (request == "StopRecording")
		return UiOperation::StopRecording;
	if (request == "PauseRecording")
		return UiOperation::PauseRecording;
	if (request == "ResumeRecording")
		return UiOperation::ResumeRecording;
	if (request == "StartSceneRecordings")
		return UiOperation::StartSceneRecordings;
	if (request == "GetSceneRecordingSession")
		return UiOperation::GetSceneRecordingSession;
	if (request == "StopSceneRecordings")
		return UiOperation::StopSceneRecordings;
	if (request == "GetStreamingStatus")
		return UiOperation::StreamingStatus;
	if (request == "StartStreaming")
		return UiOperation::StartStreaming;
	if (request == "StopStreaming")
		return UiOperation::StopStreaming;
	if (request == "GetReplayBufferStatus")
		return UiOperation::ReplayBufferStatus;
	if (request == "StartReplayBuffer")
		return UiOperation::StartReplayBuffer;
	if (request == "StopReplayBuffer")
		return UiOperation::StopReplayBuffer;
	if (request == "SaveReplayBuffer")
		return UiOperation::SaveReplayBuffer;
	if (request == "GetVirtualCameraStatus")
		return UiOperation::VirtualCameraStatus;
	if (request == "StartVirtualCamera")
		return UiOperation::StartVirtualCamera;
	if (request == "StopVirtualCamera")
		return UiOperation::StopVirtualCamera;
	if (request == "ListOutputs")
		return UiOperation::ListOutputs;
	if (request == "GetOutputStatus")
		return UiOperation::OutputStatus;
	if (request == "StartOutput")
		return UiOperation::StartOutput;
	if (request == "StopOutput")
		return UiOperation::StopOutput;
	if (request == "ListProfiles")
		return UiOperation::ListProfiles;
	if (request == "GetCurrentProfile")
		return UiOperation::ProfileStatus;
	if (request == "SetCurrentProfile")
		return UiOperation::SetProfile;
	if (request == "ListSceneCollections")
		return UiOperation::ListSceneCollections;
	if (request == "GetCurrentSceneCollection")
		return UiOperation::SceneCollectionStatus;
	if (request == "SetCurrentSceneCollection")
		return UiOperation::SetSceneCollection;
	if (request == "ListAllowlistedHotkeys")
		return UiOperation::ListAllowlistedHotkeys;
	if (request == "TriggerAllowlistedHotkey")
		return UiOperation::TriggerAllowlistedHotkey;
	if (request == "CaptureSourceScreenshot" || request == "CaptureScreenshot")
		return UiOperation::CaptureScreenshot;
	if (request == "CaptureProgramFrame")
		return UiOperation::CaptureProgramFrame;
	return UiOperation::Invalid;
}

void request_frontend_exit(void *)
{
	auto *main_window = static_cast<QWidget *>(obs_frontend_get_main_window());
	if (main_window != nullptr)
		QMetaObject::invokeMethod(main_window, "close", Qt::QueuedConnection);
}

void vendor_request(obs_data_t *request_data, obs_data_t *response_data, void *private_data)
{
	const auto *request = static_cast<const char *>(private_data);
	std::string scene_name;
	std::string output_name;
	std::string target_name;
	std::string hotkey_name;
	std::string source_name;
	std::string source_kind;
	std::string transition_name;
	std::string overlay_anchor;
	int overlay_opacity = 78;
	int overlay_margin = 48;
	std::string window_executable_filter;
	std::string window_title_filter;
	WindowCaptureBinding window_capture;
	WindowCaptureBinding expected_window_capture;
	dcc_mcp_obs::AgentInputActivity agent_input_activity;
	dcc_mcp_obs::TypedSourceRequest typed_source_request;
	std::vector<dcc_mcp_obs::SceneRecordingSpec> scene_recording_specs;
	std::string scene_recording_session_id;
	int64_t scene_item_id = 0;
	bool enabled = true, studio_enabled = false, has_duration = false;
	int duration_ms = 0;
	bool has_pos = false, has_scale = false, has_rotation = false;
	float pos_x = 0.0f, pos_y = 0.0f, scale_x = 1.0f, scale_y = 1.0f, rotation = 0.0f;
	const uint64_t now_ms = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
							      std::chrono::system_clock::now().time_since_epoch())
							      .count());
	uint64_t deadline_at_ms = now_ms + 5000;
	if (request_data != nullptr && obs_data_has_user_value(request_data, "__dccDeadlineAtMs")) {
		const long long requested_deadline_at_ms = obs_data_get_int(request_data, "__dccDeadlineAtMs");
		if (requested_deadline_at_ms <= 0 ||
		    static_cast<uint64_t>(requested_deadline_at_ms) > now_ms + 120000) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		deadline_at_ms = static_cast<uint64_t>(requested_deadline_at_ms);
	}
	if (std::string(request) == "ListSources" && request_data != nullptr) {
		const char *value = obs_data_get_string(request_data, "sceneName");
		if (value != nullptr)
			scene_name = value;
		if (scene_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if ((std::string(request) == "GetOutputStatus" || std::string(request) == "StartOutput" ||
	     std::string(request) == "StopOutput") &&
	    request_data != nullptr) {
		const char *value = obs_data_get_string(request_data, "outputName");
		if (value != nullptr)
			output_name = value;
		if (output_name.empty() || output_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	const std::string request_name(request);
	if (dcc_mcp_obs::is_typed_source_request(request_name)) {
		const char *error_code = nullptr;
		if (!dcc_mcp_obs::parse_typed_source_request(request_name, request_data, typed_source_request,
							     error_code)) {
			set_error(response_data, error_code != nullptr ? error_code : "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "StartSceneRecordings") {
		obs_data_array_t *recordings = request_data != nullptr ? obs_data_get_array(request_data, "recordings")
								       : nullptr;
		const size_t count = recordings != nullptr ? obs_data_array_count(recordings) : 0;
		std::set<std::string> scenes;
		std::set<std::string> prefixes;
		bool valid = count >= 1 && count <= 8;
		for (size_t index = 0; valid && index < count; ++index) {
			obs_data_t *item = obs_data_array_item(recordings, index);
			const char *scene = item != nullptr ? obs_data_get_string(item, "sceneName") : nullptr;
			const char *prefix = item != nullptr ? obs_data_get_string(item, "fileNamePrefix") : nullptr;
			dcc_mcp_obs::SceneRecordingSpec spec{scene != nullptr ? scene : "",
							     prefix != nullptr ? prefix : ""};
			std::string folded = spec.file_name_prefix;
			std::transform(folded.begin(), folded.end(), folded.begin(), [](unsigned char character) {
				return static_cast<char>(std::tolower(character));
			});
			const std::string invalid = "<>:\"/\\|?*";
			valid = !spec.scene_name.empty() && spec.scene_name.size() <= 256 &&
				!spec.file_name_prefix.empty() && spec.file_name_prefix.size() <= 96 &&
				spec.file_name_prefix.front() != ' ' && spec.file_name_prefix.back() != ' ' &&
				spec.file_name_prefix.back() != '.' &&
				std::none_of(spec.file_name_prefix.begin(), spec.file_name_prefix.end(),
					     [&](unsigned char character) {
						     return character < 32 || character == 127 ||
							    invalid.find(static_cast<char>(character)) !=
								    std::string::npos;
					     }) &&
				scenes.insert(spec.scene_name).second && prefixes.insert(folded).second;
			if (valid)
				scene_recording_specs.push_back(std::move(spec));
			if (item != nullptr)
				obs_data_release(item);
		}
		if (recordings != nullptr)
			obs_data_array_release(recordings);
		if (!valid) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "GetSceneRecordingSession" || request_name == "StopSceneRecordings") {
		const char *value = request_data != nullptr ? obs_data_get_string(request_data, "sessionId") : nullptr;
		scene_recording_session_id = value != nullptr ? value : "";
		const bool valid = !scene_recording_session_id.empty() && scene_recording_session_id.size() <= 128 &&
				   std::all_of(scene_recording_session_id.begin(), scene_recording_session_id.end(),
					       [](unsigned char character) {
						       return std::isalnum(character) || character == '-' ||
							      character == '_';
					       });
		if (!valid) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "ListWindowCaptureCandidates" || request_name == "RestoreWindowCaptureCandidate") {
		const char *executable = request_data != nullptr ? obs_data_get_string(request_data, "executable")
								 : nullptr;
		const char *window_title = request_data != nullptr ? obs_data_get_string(request_data, "windowTitle")
								   : nullptr;
		if (executable == nullptr || !*executable || std::string(executable).size() > 256 ||
		    std::strpbrk(executable, "/\\:") != nullptr ||
		    ((request_name == "RestoreWindowCaptureCandidate" ||
		      obs_data_has_user_value(request_data, "windowTitle")) &&
		     (window_title == nullptr || !*window_title || std::string(window_title).size() > 256))) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		window_executable_filter = executable;
		if (window_title != nullptr)
			window_title_filter = window_title;
	}
	if (request_name == "RestoreWindowCaptureCandidate") {
		const long long process_id = request_data != nullptr ? obs_data_get_int(request_data, "processId") : 0;
		const long long window_handle = request_data != nullptr ? obs_data_get_int(request_data, "windowHandle")
									: 0;
		if (process_id <= 0 || static_cast<uint64_t>(process_id) > std::numeric_limits<uint32_t>::max() ||
		    window_handle <= 0 ||
		    static_cast<uint64_t>(window_handle) > static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		window_capture.process_id = static_cast<uint32_t>(process_id);
		window_capture.window_handle = static_cast<uint64_t>(window_handle);
		window_capture.window_title = window_title_filter;
	}
	if (request_name == "SetCurrentProfile" || request_name == "SetCurrentSceneCollection") {
		const char *value = nullptr;
		if (request_data != nullptr) {
			value = request_name == "SetCurrentProfile"
					? obs_data_get_string(request_data, "profileName")
					: obs_data_get_string(request_data, "sceneCollectionName");
			if (value == nullptr || !*value)
				value = obs_data_get_string(request_data, "name");
		}
		if (value != nullptr)
			target_name = value;
		if (target_name.empty() || target_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "TriggerAllowlistedHotkey") {
		const char *value = request_data != nullptr ? obs_data_get_string(request_data, "hotkeyName") : nullptr;
		if ((value == nullptr || !*value) && request_data != nullptr)
			value = obs_data_get_string(request_data, "hotkeyId");
		if (value != nullptr)
			hotkey_name = value;
		if (hotkey_name.empty() || hotkey_name.size() > 128) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	const bool scene_request =
		request_name == "SetCurrentScene" || request_name == "GetCurrentScene" ||
		request_name == "CreateScene" || request_name == "RenameScene" || request_name == "RemoveScene" ||
		request_name == "ListSceneItems" || request_name == "GetSceneItem" ||
		request_name == "CreateSceneItem" || request_name == "CreateWindowCaptureSource" ||
		request_name == "GetWindowCaptureSource" || request_name == "RebindWindowCaptureSource" ||
		request_name == "SetWindowCaptureMethod" || request_name == "SetSceneItemEnabled" ||
		request_name == "SetSceneItemTransform" || request_name == "RemoveSceneItem" ||
		request_name == "TriggerTransition" || request_name == "SetCurrentPreviewScene" ||
		request_name == "CreateAgentInputOverlay" || request_name == "GetAgentInputOverlay" ||
		request_name == "SetAgentInputOverlayLayout" || request_name == "EmitAgentInputActivity" ||
		request_name == "ClearAgentInputOverlay";
	if (scene_request && request_data != nullptr) {
		const char *value = obs_data_get_string(request_data, "sceneName");
		if (value != nullptr)
			scene_name = value;
		if (scene_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		value = obs_data_get_string(request_data, "newSceneName");
		if (value == nullptr)
			value = obs_data_get_string(request_data, "targetSceneName");
		if (value == nullptr)
			value = obs_data_get_string(request_data, "newName");
		if (value != nullptr)
			target_name = value;
		value = obs_data_get_string(request_data, "sourceName");
		if (value != nullptr)
			source_name = value;
		value = obs_data_get_string(request_data, "sourceKind");
		if (value != nullptr)
			source_kind = value;
		if (request_name == "CreateSceneItem" && (scene_name.empty() || source_name.empty())) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		if (request_name == "CreateWindowCaptureSource" || request_name == "GetWindowCaptureSource" ||
		    request_name == "RebindWindowCaptureSource" || request_name == "SetWindowCaptureMethod") {
			const long long process_id = obs_data_get_int(request_data, "processId");
			const long long window_handle = obs_data_get_int(request_data, "windowHandle");
			const char *window_title = obs_data_get_string(request_data, "windowTitle");
			const char *capture_method = obs_data_get_string(request_data, "captureMethod");
			if (scene_name.empty() || source_name.empty() || process_id <= 0 ||
			    static_cast<uint64_t>(process_id) > std::numeric_limits<uint32_t>::max() ||
			    window_handle <= 0 ||
			    static_cast<uint64_t>(window_handle) >
				    static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) ||
			    window_title == nullptr || !*window_title || std::string(window_title).size() > 256 ||
			    capture_method == nullptr || window_capture_method_value(capture_method) < 0 ||
			    !obs_data_has_user_value(request_data, "captureCursor") ||
			    !obs_data_has_user_value(request_data, "clientArea") ||
			    !obs_data_has_user_value(request_data, "enabled")) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
			window_capture.process_id = static_cast<uint32_t>(process_id);
			window_capture.window_handle = static_cast<uint64_t>(window_handle);
			window_capture.window_title = window_title;
			window_capture.capture_method = capture_method;
			window_capture.capture_cursor = obs_data_get_bool(request_data, "captureCursor");
			window_capture.client_area = obs_data_get_bool(request_data, "clientArea");
			enabled = obs_data_get_bool(request_data, "enabled");
		}
		if (request_name == "RebindWindowCaptureSource") {
			const long long process_id = obs_data_get_int(request_data, "expectedProcessId");
			const long long window_handle = obs_data_get_int(request_data, "expectedWindowHandle");
			const char *window_title = obs_data_get_string(request_data, "expectedWindowTitle");
			if (process_id <= 0 ||
			    static_cast<uint64_t>(process_id) > std::numeric_limits<uint32_t>::max() ||
			    window_handle <= 0 ||
			    static_cast<uint64_t>(window_handle) >
				    static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) ||
			    window_title == nullptr || !*window_title || std::string(window_title).size() > 256) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
			expected_window_capture.process_id = static_cast<uint32_t>(process_id);
			expected_window_capture.window_handle = static_cast<uint64_t>(window_handle);
			expected_window_capture.window_title = window_title;
		}
		if (request_name == "GetSceneItem" || request_name == "SetSceneItemEnabled" ||
		    request_name == "SetSceneItemTransform" || request_name == "RemoveSceneItem") {
			scene_item_id = obs_data_get_int(request_data, "sceneItemId");
			if (scene_item_id <= 0 || scene_name.empty()) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
		}
		if (request_name == "SetSceneItemEnabled")
			enabled = obs_data_get_bool(request_data, "enabled");
		if (request_name == "SetSceneItemTransform") {
			if (obs_data_has_user_value(request_data, "posX")) {
				has_pos = true;
				pos_x = static_cast<float>(obs_data_get_double(request_data, "posX"));
			}
			if (obs_data_has_user_value(request_data, "posY")) {
				has_pos = true;
				pos_y = static_cast<float>(obs_data_get_double(request_data, "posY"));
			}
			if (obs_data_has_user_value(request_data, "scaleX")) {
				has_scale = true;
				scale_x = static_cast<float>(obs_data_get_double(request_data, "scaleX"));
			}
			if (obs_data_has_user_value(request_data, "scaleY")) {
				has_scale = true;
				scale_y = static_cast<float>(obs_data_get_double(request_data, "scaleY"));
			}
			if (obs_data_has_user_value(request_data, "rotation")) {
				has_rotation = true;
				rotation = static_cast<float>(obs_data_get_double(request_data, "rotation"));
			}
			if ((!has_pos && !has_scale && !has_rotation) || !std::isfinite(pos_x) ||
			    !std::isfinite(pos_y) || !std::isfinite(scale_x) || !std::isfinite(scale_y) ||
			    !std::isfinite(rotation)) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
		}
	}
	const bool overlay_request =
		request_name == "CreateAgentInputOverlay" || request_name == "GetAgentInputOverlay" ||
		request_name == "SetAgentInputOverlayLayout" || request_name == "EmitAgentInputActivity" ||
		request_name == "ClearAgentInputOverlay";
	if (overlay_request &&
	    (scene_name.empty() || source_name.empty() || scene_name.size() > 256 || source_name.size() > 256)) {
		set_error(response_data, "OBS_ARGUMENT_INVALID");
		return;
	}
	if (request_name == "CreateAgentInputOverlay" || request_name == "SetAgentInputOverlayLayout") {
		const char *value = request_data != nullptr ? obs_data_get_string(request_data, "anchor") : nullptr;
		if (value != nullptr)
			overlay_anchor = value;
		const bool valid_anchor = overlay_anchor == "top_left" || overlay_anchor == "top_center" ||
					  overlay_anchor == "top_right" || overlay_anchor == "center_left" ||
					  overlay_anchor == "center_right" || overlay_anchor == "bottom_left" ||
					  overlay_anchor == "bottom_center" || overlay_anchor == "bottom_right";
		if (!valid_anchor) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		if (request_name == "SetAgentInputOverlayLayout") {
			overlay_opacity = request_data != nullptr
						  ? static_cast<int>(obs_data_get_int(request_data, "opacity"))
						  : 0;
			overlay_margin = request_data != nullptr
						 ? static_cast<int>(obs_data_get_int(request_data, "margin"))
						 : 0;
			if (overlay_opacity < 20 || overlay_opacity > 100 || overlay_margin < 8 ||
			    overlay_margin > 160) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
		}
	}
	if (request_name == "EmitAgentInputActivity") {
		const char *agent_id = request_data != nullptr ? obs_data_get_string(request_data, "agentId") : nullptr;
		const char *event_kind = request_data != nullptr ? obs_data_get_string(request_data, "eventKind")
								 : nullptr;
		const char *mouse_button = request_data != nullptr ? obs_data_get_string(request_data, "mouseButton")
								   : nullptr;
		const char *wheel_direction =
			request_data != nullptr ? obs_data_get_string(request_data, "wheelDirection") : nullptr;
		agent_input_activity.event_kind = event_kind != nullptr ? event_kind : "";
		agent_input_activity.agent_id = agent_id != nullptr ? agent_id : "";
		agent_input_activity.mouse_button = mouse_button != nullptr ? mouse_button : "";
		agent_input_activity.wheel_direction = wheel_direction != nullptr ? wheel_direction : "";
		agent_input_activity.character_count =
			request_data != nullptr ? static_cast<int>(obs_data_get_int(request_data, "characterCount"))
						: 0;
		agent_input_activity.duration_ms =
			request_data != nullptr ? static_cast<int>(obs_data_get_int(request_data, "durationMs")) : 0;
		const char *keys_csv = request_data != nullptr ? obs_data_get_string(request_data, "keysCsv") : nullptr;
		std::string remaining_keys = keys_csv != nullptr ? keys_csv : "";
		bool keys_valid = remaining_keys.size() <= 32;
		while (!remaining_keys.empty() && agent_input_activity.keys.size() < 5) {
			const size_t separator = remaining_keys.find(',');
			const std::string key = remaining_keys.substr(0, separator);
			if (!valid_agent_input_key(key)) {
				keys_valid = false;
				agent_input_activity.keys.clear();
				break;
			}
			agent_input_activity.keys.push_back(key);
			if (separator == std::string::npos)
				remaining_keys.clear();
			else
				remaining_keys.erase(0, separator + 1);
		}
		if (agent_input_activity.keys.size() > 4) {
			keys_valid = false;
			agent_input_activity.keys.clear();
		}
		const bool valid_kind = agent_input_activity.event_kind == "shortcut" ||
					agent_input_activity.event_kind == "mouse_button" ||
					agent_input_activity.event_kind == "mouse_wheel" ||
					agent_input_activity.event_kind == "typing";
		const bool valid_mouse =
			agent_input_activity.mouse_button == "none" || agent_input_activity.mouse_button == "left" ||
			agent_input_activity.mouse_button == "right" || agent_input_activity.mouse_button == "middle" ||
			agent_input_activity.mouse_button == "back" || agent_input_activity.mouse_button == "forward";
		const bool valid_wheel = agent_input_activity.wheel_direction == "none" ||
					 agent_input_activity.wheel_direction == "up" ||
					 agent_input_activity.wheel_direction == "down" ||
					 agent_input_activity.wheel_direction == "left" ||
					 agent_input_activity.wheel_direction == "right";
		const bool semantic_valid =
			(agent_input_activity.event_kind == "shortcut" && !agent_input_activity.keys.empty() &&
			 agent_input_activity.mouse_button == "none" &&
			 agent_input_activity.wheel_direction == "none" && agent_input_activity.character_count == 0) ||
			(agent_input_activity.event_kind == "mouse_button" && agent_input_activity.keys.empty() &&
			 agent_input_activity.mouse_button != "none" &&
			 agent_input_activity.wheel_direction == "none" && agent_input_activity.character_count == 0) ||
			(agent_input_activity.event_kind == "mouse_wheel" && agent_input_activity.keys.empty() &&
			 agent_input_activity.mouse_button == "none" &&
			 agent_input_activity.wheel_direction != "none" && agent_input_activity.character_count == 0) ||
			(agent_input_activity.event_kind == "typing" && agent_input_activity.keys.empty() &&
			 agent_input_activity.mouse_button == "none" &&
			 agent_input_activity.wheel_direction == "none" && agent_input_activity.character_count >= 1 &&
			 agent_input_activity.character_count <= 10000);
		const bool valid_agent_id =
			!agent_input_activity.agent_id.empty() && agent_input_activity.agent_id.size() <= 64 &&
			std::all_of(agent_input_activity.agent_id.begin(), agent_input_activity.agent_id.end(),
				    [](unsigned char value) { return value >= 32 && value != 127; });
		if (!valid_kind || !valid_mouse || !valid_wheel || !keys_valid || !semantic_valid || !valid_agent_id ||
		    agent_input_activity.duration_ms < 250 || agent_input_activity.duration_ms > 5000) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	const char *required_capability = nullptr;
	if (request_name == "SetCurrentScene" || request_name == "CreateScene" || request_name == "RenameScene" ||
	    request_name == "RemoveScene" || request_name == "CreateSceneItem" ||
	    request_name == "CreateWindowCaptureSource" || request_name == "GetWindowCaptureSource" ||
	    request_name == "RestoreWindowCaptureCandidate" || request_name == "RebindWindowCaptureSource" ||
	    request_name == "SetWindowCaptureMethod" || request_name == "SetSceneItemEnabled" ||
	    request_name == "SetSceneItemTransform" || request_name == "RemoveSceneItem")
		required_capability = request_name == "SetCurrentScene"
					      ? "scene_switch"
					      : (request_name == "CreateWindowCaptureSource" ||
								 request_name == "GetWindowCaptureSource" ||
								 request_name == "RestoreWindowCaptureCandidate" ||
								 request_name == "RebindWindowCaptureSource" ||
								 request_name == "SetWindowCaptureMethod"
							 ? "window_capture"
							 : "scene_graph");
	else if (request_name == "SetCurrentTransition" || request_name == "TriggerTransition")
		required_capability = "transitions";
	else if (request_name == "SetStudioMode")
		required_capability = "studio_mode";
	else if (request_name == "SetCurrentPreviewScene")
		required_capability = "studio_preview";
	else if (request_name == "TriggerStudioModeTransition")
		required_capability = "studio_transition";
	else if (request_name == "RequestGracefulShutdown")
		required_capability = "application_lifecycle";
	else if (request_name == "CreateAgentInputOverlay" || request_name == "SetAgentInputOverlayLayout" ||
		 request_name == "EmitAgentInputActivity" || request_name == "ClearAgentInputOverlay")
		required_capability = "agent_input_overlay";
	if (required_capability != nullptr) {
		const char *capability = request_data != nullptr ? obs_data_get_string(request_data, "capability")
								 : nullptr;
		if (capability == nullptr || std::string(capability) != required_capability) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "SetCurrentTransition" && request_data != nullptr) {
		const char *value = obs_data_get_string(request_data, "transitionName");
		if (value != nullptr)
			transition_name = value;
		if (transition_name.empty() || transition_name.size() > 256) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
		if (obs_data_has_user_value(request_data, "durationMs")) {
			duration_ms = static_cast<int>(obs_data_get_int(request_data, "durationMs"));
			if (duration_ms < 0 || duration_ms > 3600000) {
				set_error(response_data, "OBS_ARGUMENT_INVALID");
				return;
			}
			has_duration = true;
		}
	}
	if (request_name == "SetStudioMode" && request_data != nullptr)
		studio_enabled = obs_data_get_bool(request_data, "studioModeEnabled");
	std::string image_format = "png";
	if (request_name == "CaptureProgramFrame") {
		const char *format = request_data != nullptr ? obs_data_get_string(request_data, "imageFormat")
							     : nullptr;
		const long long width = request_data != nullptr ? obs_data_get_int(request_data, "imageWidth") : 0;
		const long long height = request_data != nullptr ? obs_data_get_int(request_data, "imageHeight") : 0;
		if (format == nullptr || std::string(format) != "png" || width != 320 || height != 180) {
			set_error(response_data, "OBS_ARGUMENT_INVALID");
			return;
		}
	}
	if (request_name == "CaptureSourceScreenshot" || request_name == "CaptureScreenshot") {
		const char *source = request_data != nullptr ? obs_data_get_string(request_data, "sourceName")
							     : nullptr;
		if (source != nullptr)
			target_name = source;
		const char *format = request_data != nullptr ? obs_data_get_string(request_data, "imageFormat")
							     : nullptr;
		if (format != nullptr && *format)
			image_format = format;
		if (target_name.empty() || target_name.size() > 256 ||
		    (image_format != "png" && image_format != "jpg" && image_format != "jpeg" &&
		     image_format != "webp")) {
			set_error(response_data, "OBS_SCREENSHOT_INVALID");
			return;
		}
	}
	run_ui_operation(operation_for(request), scene_name, output_name, target_name, hotkey_name, image_format,
			 source_name, source_kind, transition_name, window_executable_filter, window_title_filter,
			 scene_item_id, enabled, studio_enabled, has_duration, duration_ms, has_pos, has_scale,
			 has_rotation, pos_x, pos_y, scale_x, scale_y, rotation, window_capture,
			 expected_window_capture, overlay_anchor, overlay_opacity, overlay_margin, agent_input_activity,
			 scene_recording_specs, scene_recording_session_id, typed_source_request, deadline_at_ms,
			 response_data);
	if (request_name == "RequestGracefulShutdown" && obs_data_get_bool(response_data, "shutdownScheduled"))
		obs_queue_task(OBS_TASK_UI, request_frontend_exit, nullptr, false);
}

void unregister_vendor_requests();

void frontend_event(enum obs_frontend_event event, void *)
{
	if (event == OBS_FRONTEND_EVENT_EXIT) {
		// obs-websocket can unload before this module. Its vendor handle is no
		// longer safe once frontend shutdown advances to module teardown, so
		// unregister while the provider is still alive and never call it later.
		unregister_vendor_requests();
		return;
	}
	const uint64_t event_sequence = g_event_sequence.fetch_add(1) + 1;
	if (g_vendor != nullptr) {
		obs_data_t *event = obs_data_create();
		set_identity(event, event_sequence);
		obs_websocket_vendor_emit_event(g_vendor, "HostStateChanged", event);
		obs_data_release(event);
	}
}

constexpr const char *kRequests[] = {
	"GetPluginStatus",
	"RequestGracefulShutdown",
	"CreateAgentInputOverlay",
	"GetAgentInputOverlay",
	"SetAgentInputOverlayLayout",
	"EmitAgentInputActivity",
	"ClearAgentInputOverlay",
	"ListScenes",
	"SetCurrentScene",
	"GetCurrentScene",
	"CreateScene",
	"RenameScene",
	"RemoveScene",
	"ListSceneItems",
	"GetSceneItem",
	"CreateSceneItem",
	"ListWindowCaptureCandidates",
	"RestoreWindowCaptureCandidate",
	"CreateWindowCaptureSource",
	"GetWindowCaptureSource",
	"RebindWindowCaptureSource",
	"SetWindowCaptureMethod",
	"SetSceneItemEnabled",
	"SetSceneItemTransform",
	"RemoveSceneItem",
	"ListTransitions",
	"GetCurrentTransition",
	"SetCurrentTransition",
	"TriggerTransition",
	"GetStudioModeStatus",
	"SetStudioMode",
	"GetCurrentPreviewScene",
	"SetCurrentPreviewScene",
	"TriggerStudioModeTransition",
	"ListSources",
	"GetRecordingStatus",
	"GetOperatorStatus",
	"StartRecording",
	"StopRecording",
	"PauseRecording",
	"ResumeRecording",
	"StartSceneRecordings",
	"GetSceneRecordingSession",
	"StopSceneRecordings",
	"GetStreamingStatus",
	"StartStreaming",
	"StopStreaming",
	"GetReplayBufferStatus",
	"StartReplayBuffer",
	"StopReplayBuffer",
	"SaveReplayBuffer",
	"GetVirtualCameraStatus",
	"StartVirtualCamera",
	"StopVirtualCamera",
	"ListOutputs",
	"GetOutputStatus",
	"StartOutput",
	"StopOutput",
	"ListProfiles",
	"GetCurrentProfile",
	"SetCurrentProfile",
	"ListSceneCollections",
	"GetCurrentSceneCollection",
	"SetCurrentSceneCollection",
	"ListAllowlistedHotkeys",
	"TriggerAllowlistedHotkey",
	"CaptureSourceScreenshot",
	"CaptureScreenshot",
	"CaptureProgramFrame",
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

void unregister_vendor_requests()
{
	if (g_vendor == nullptr)
		return;
	for (const char *request : kRequests)
		obs_websocket_vendor_unregister_request(g_vendor, request);
	g_vendor = nullptr;
}

} // namespace

const char *obs_module_description(void)
{
	return "Native typed DCC-MCP control bridge for OBS Studio";
}

bool obs_module_load(void)
{
	g_plugin_loaded.store(true);
	g_instance_id = make_instance_id();
	g_scene_recording_sessions = std::make_unique<dcc_mcp_obs::SceneRecordingSessionManager>();
	dcc_mcp_obs::register_agent_input_overlay_source();
	obs_frontend_add_event_callback(frontend_event, nullptr);
	blog(LOG_INFO, "dcc-mcp-obs native plugin loaded");
	return true;
}

void obs_module_post_load(void)
{
	obs_queue_task(OBS_TASK_UI, install_dcc_mcp_menu, nullptr, true);
	g_vendor = obs_websocket_register_vendor(kVendorName);
	if (g_vendor == nullptr) {
		blog(LOG_ERROR, "dcc-mcp-obs requires obs-websocket API v3");
		return;
	}
	for (const char *request : kRequests)
		obs_websocket_vendor_register_request(g_vendor, request, vendor_request, const_cast<char *>(request));
	obs_queue_task(OBS_TASK_UI, start_configured_sidecar, nullptr, true);
}

void obs_module_unload(void)
{
	g_plugin_loaded.store(false);
	obs_queue_task(OBS_TASK_UI, stop_scene_recording_sessions, nullptr, true);
	obs_queue_task(OBS_TASK_UI, stop_configured_sidecar, nullptr, true);
	obs_queue_task(OBS_TASK_UI, remove_dcc_mcp_menu, nullptr, true);
	obs_frontend_remove_event_callback(frontend_event, nullptr);
	// The obs-websocket module may already be gone at this point. Provider API
	// calls are forbidden during module teardown; its vendor registry owns the
	// remaining request records and is torn down with the provider.
	g_vendor = nullptr;
	blog(LOG_INFO, "dcc-mcp-obs native plugin unloaded");
}
