#include "scene-recording-session.hpp"
#include "scene-recording-dimensions.hpp"
#include "agent-input-overlay.hpp"
#include "presentation-overlay.hpp"

#include <obs-frontend-api.h>
#include <util/config-file.h>

#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QFile>
#include <QString>
#include <QUuid>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <set>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif

namespace dcc_mcp_obs {
namespace {

constexpr size_t kMaxSceneRecordings = 8;

void set_error(obs_data_t *data, const char *code)
{
	obs_data_set_bool(data, "ok", false);
	obs_data_set_string(data, "errorCode", code);
}

bool valid_name(const std::string &value, size_t maximum)
{
	return !value.empty() && value.size() <= maximum &&
	       std::all_of(value.begin(), value.end(),
			   [](unsigned char character) { return character >= 32 && character != 127; });
}

bool valid_prefix(const std::string &value)
{
	static const std::string invalid = "<>:\"/\\|?*";
	return valid_name(value, 96) && value.front() != ' ' && value.back() != ' ' && value.back() != '.' &&
	       std::none_of(value.begin(), value.end(), [&](unsigned char character) {
		       return invalid.find(static_cast<char>(character)) != std::string::npos;
	       });
}

bool valid_file_name(const std::string &value)
{
	if (value.empty() || value.size() > 160 || value.front() == ' ' || value.back() == ' ' || value.back() == '.')
		return false;
	const std::string invalid = "<>:\"/\\|?*";
	if (std::any_of(value.begin(), value.end(), [&](unsigned char character) {
		    return character < 32 || character == 127 ||
			   invalid.find(static_cast<char>(character)) != std::string::npos;
	    }))
		return false;
	return value.size() >= 4 && std::equal(value.end() - 4, value.end(), ".mp4", [](char left, char right) {
		       return std::tolower(static_cast<unsigned char>(left)) ==
			      std::tolower(static_cast<unsigned char>(right));
	       });
}

bool valid_optional_id(const std::string &value)
{
	return value.empty() ||
	       (value.size() <= 128 && std::all_of(value.begin(), value.end(), [](unsigned char character) {
			return std::isalnum(character) || character == '-' || character == '_' || character == '.' ||
			       character == ':';
		}));
}

bool valid_optional_path(const std::string &value)
{
	return value.empty() ||
	       (value.size() <= 4096 && std::none_of(value.begin(), value.end(), [](unsigned char character) {
			return character < 32 || character == 127;
		}));
}

bool exact_window_is_live(uint32_t process_id, uint64_t window_handle)
{
	if (process_id == 0 || window_handle == 0)
		return false;
#ifdef _WIN32
	const auto window = reinterpret_cast<HWND>(static_cast<uintptr_t>(window_handle));
	DWORD actual_process_id = 0;
	return IsWindow(window) != FALSE && GetWindowThreadProcessId(window, &actual_process_id) != 0 &&
	       actual_process_id == process_id;
#else
	return false;
#endif
}

bool exact_window_client_size(uint32_t process_id, uint64_t window_handle, uint32_t &width, uint32_t &height)
{
#ifdef _WIN32
	const auto window = reinterpret_cast<HWND>(static_cast<uintptr_t>(window_handle));
	DWORD actual_process_id = 0;
	RECT client{};
	if (process_id == 0 || window_handle == 0 || IsWindow(window) == FALSE || IsWindowVisible(window) == FALSE ||
	    IsIconic(window) != FALSE || GetWindowThreadProcessId(window, &actual_process_id) == 0 ||
	    actual_process_id != process_id || GetClientRect(window, &client) == FALSE || client.right <= client.left ||
	    client.bottom <= client.top)
		return false;
	width = static_cast<uint32_t>(client.right - client.left);
	height = static_cast<uint32_t>(client.bottom - client.top);
	return true;
#else
	UNUSED_PARAMETER(process_id);
	UNUSED_PARAMETER(window_handle);
	UNUSED_PARAMETER(width);
	UNUSED_PARAMETER(height);
	return false;
#endif
}

QString recording_directory()
{
	config_t *config = obs_frontend_get_profile_config();
	if (config == nullptr)
		return {};
	const char *mode = config_get_string(config, "Output", "Mode");
	const bool simple = mode == nullptr || std::string(mode) != "Advanced";
	const char *path = simple ? config_get_string(config, "SimpleOutput", "FilePath")
				  : config_get_string(config, "AdvOut", "RecFilePath");
	return path != nullptr ? QString::fromUtf8(path).trimmed() : QString{};
}

obs_data_t *encoder_settings()
{
	obs_data_t *settings = obs_data_create();
	obs_data_set_string(settings, "rate_control", "CBR");
	obs_data_set_int(settings, "bitrate", 6000);
	obs_data_set_int(settings, "keyint_sec", 2);
	obs_data_set_string(settings, "preset", "ultrafast");
	obs_data_set_string(settings, "profile", "high");
	return settings;
}

bool silent_audio_input(void *, uint64_t start_ts, uint64_t, uint64_t *new_ts, uint32_t, struct audio_output_data *)
{
	*new_ts = start_ts;
	return true;
}

struct SceneSources {
	obs_source_t *capture = nullptr;
	obs_source_t *agent_overlay = nullptr;
	obs_source_t *presentation_overlay = nullptr;
	bool ambiguous_capture = false;
	bool ambiguous_agent_overlay = false;
	bool ambiguous_presentation_overlay = false;
};

bool collect_scene_sources(obs_scene_t *, obs_sceneitem_t *item, void *private_data)
{
	if (!obs_sceneitem_visible(item))
		return true;
	auto *sources = static_cast<SceneSources *>(private_data);
	obs_source_t *source = obs_sceneitem_get_source(item);
	const char *id = source != nullptr ? obs_source_get_id(source) : nullptr;
	if (id == nullptr)
		return true;
	if (std::string(id) == "window_capture") {
		if (sources->capture != nullptr)
			sources->ambiguous_capture = true;
		else
			sources->capture = source;
	} else if (std::string(id) == kAgentInputOverlaySourceId) {
		if (sources->agent_overlay != nullptr)
			sources->ambiguous_agent_overlay = true;
		else
			sources->agent_overlay = source;
	} else if (std::string(id) == kPresentationOverlaySourceId) {
		if (sources->presentation_overlay != nullptr)
			sources->ambiguous_presentation_overlay = true;
		else
			sources->presentation_overlay = source;
	}
	return true;
}

void position_overlay(obs_sceneitem_t *item, obs_source_t *source, uint32_t width, uint32_t height,
		      float maximum_width_fraction, float maximum_height_fraction)
{
	obs_data_t *settings = obs_source_get_settings(source);
	const std::string anchor = obs_data_get_string(settings, "anchor");
	const float margin = static_cast<float>(std::clamp<long long>(obs_data_get_int(settings, "margin"), 8, 160));
	obs_data_release(settings);
	const float source_width = static_cast<float>(std::max<uint32_t>(1, obs_source_get_width(source)));
	const float source_height = static_cast<float>(std::max<uint32_t>(1, obs_source_get_height(source)));
	const float scale_value =
		std::min(1.0f, std::min(static_cast<float>(width) * maximum_width_fraction / source_width,
					static_cast<float>(height) * maximum_height_fraction / source_height));
	const float rendered_width = source_width * scale_value;
	const float rendered_height = source_height * scale_value;
	const float left = margin;
	const float center = std::max(0.0f, (static_cast<float>(width) - rendered_width) / 2.0f);
	const float right = std::max(0.0f, static_cast<float>(width) - rendered_width - margin);
	const float top = margin;
	const float middle = std::max(0.0f, (static_cast<float>(height) - rendered_height) / 2.0f);
	const float bottom = std::max(0.0f, static_cast<float>(height) - rendered_height - margin);
	float x = left;
	if (anchor == "top_center" || anchor == "bottom_center")
		x = center;
	else if (anchor == "top_right" || anchor == "center_right" || anchor == "bottom_right")
		x = right;
	float y = top;
	if (anchor == "center_left" || anchor == "center_right")
		y = middle;
	else if (anchor == "bottom_left" || anchor == "bottom_center" || anchor == "bottom_right")
		y = bottom;
	vec2 position{x, y};
	vec2 scale{scale_value, scale_value};
	obs_sceneitem_set_alignment(item, OBS_ALIGN_LEFT | OBS_ALIGN_TOP);
	obs_sceneitem_set_pos(item, &position);
	obs_sceneitem_set_scale(item, &scale);
	obs_sceneitem_set_order(item, OBS_ORDER_MOVE_TOP);
}

} // namespace

struct SceneRecordingSessionManager::Impl {
	struct Recording {
		std::string scene_name;
		std::string application_id;
		std::string run_id;
		std::string source_name;
		std::string file_name;
		std::string output_directory;
		std::string output_path;
		uint32_t process_id = 0;
		uint64_t window_handle = 0;
		bool binding_verified = false;
		obs_source_t *scene = nullptr;
		obs_scene_t *recording_scene = nullptr;
		obs_view_t *view = nullptr;
		video_t *video = nullptr;
		obs_encoder_t *encoder = nullptr;
		audio_t *silent_audio = nullptr;
		obs_encoder_t *silent_audio_encoder = nullptr;
		obs_output_t *output = nullptr;
		uint64_t total_bytes = 0;
		uint64_t total_frames = 0;
		std::string last_error;
		uint32_t video_width = 0;
		uint32_t video_height = 0;
		bool stop_requested = false;

		bool active() const { return output != nullptr && obs_output_active(output); }

		void capture_status()
		{
			if (output == nullptr)
				return;
			total_bytes = obs_output_get_total_bytes(output);
			total_frames = static_cast<uint64_t>(std::max(0, obs_output_get_total_frames(output)));
			const char *error = obs_output_get_last_error(output);
			if (error != nullptr && *error)
				last_error = error;
		}

		void release(bool force)
		{
			capture_status();
			if (output != nullptr && obs_output_active(output)) {
				if (force)
					obs_output_force_stop(output);
				else
					return;
			}
			if (output != nullptr) {
				obs_output_release(output);
				output = nullptr;
			}
			if (encoder != nullptr) {
				obs_encoder_release(encoder);
				encoder = nullptr;
			}
			if (silent_audio_encoder != nullptr) {
				obs_encoder_release(silent_audio_encoder);
				silent_audio_encoder = nullptr;
			}
			if (silent_audio != nullptr) {
				audio_output_close(silent_audio);
				silent_audio = nullptr;
			}
			if (view != nullptr) {
				obs_view_set_source(view, 0, nullptr);
				if (video != nullptr)
					obs_view_remove(view);
				obs_view_destroy(view);
				view = nullptr;
				video = nullptr;
			}
			if (recording_scene != nullptr) {
				obs_scene_release(recording_scene);
				recording_scene = nullptr;
			}
			if (scene != nullptr) {
				obs_source_release(scene);
				scene = nullptr;
			}
		}
	};

	struct Session {
		std::string session_id;
		std::string started_at;
		std::string stopped_at;
		std::vector<Recording> recordings;

		bool matches(const std::string &value) const { return !session_id.empty() && session_id == value; }

		bool active() const
		{
			return std::any_of(recordings.begin(), recordings.end(),
					   [](const Recording &recording) { return recording.active(); });
		}

		void release_inactive()
		{
			for (auto &recording : recordings) {
				recording.capture_status();
				if (!recording.active())
					recording.release(false);
			}
			if (!active() && stopped_at.empty())
				stopped_at = QDateTime::currentDateTime().toString(Qt::ISODate).toStdString();
		}

		void shutdown()
		{
			for (auto &recording : recordings)
				recording.release(true);
			if (stopped_at.empty())
				stopped_at = QDateTime::currentDateTime().toString(Qt::ISODate).toStdString();
		}

		obs_data_t *status_data()
		{
			release_inactive();
			obs_data_t *result = obs_data_create();
			obs_data_set_string(result, "sessionId", session_id.c_str());
			obs_data_set_bool(result, "sessionActive", active());
			obs_data_set_string(result, "startedAt", started_at.c_str());
			obs_data_set_string(result, "stoppedAt", stopped_at.c_str());
			obs_data_array_t *items = obs_data_array_create();
			for (auto &recording : recordings) {
				recording.capture_status();
				obs_data_t *item = obs_data_create();
				obs_data_set_string(item, "sceneName", recording.scene_name.c_str());
				obs_data_set_string(item, "applicationId", recording.application_id.c_str());
				obs_data_set_string(item, "runId", recording.run_id.c_str());
				obs_data_set_string(item, "sourceName", recording.source_name.c_str());
				obs_data_set_int(item, "processId", recording.process_id);
				obs_data_set_int(item, "windowHandle", static_cast<long long>(recording.window_handle));
				obs_data_set_bool(item, "bindingVerified", recording.binding_verified);
				obs_data_set_string(item, "fileName", recording.file_name.c_str());
				obs_data_set_string(item, "outputDirectory", recording.output_directory.c_str());
				obs_data_set_string(item, "outputPath", recording.output_path.c_str());
				obs_data_set_bool(item, "outputActive", recording.active());
				obs_data_set_bool(item, "videoOnly", true);
				obs_data_set_int(item, "videoWidth", recording.video_width);
				obs_data_set_int(item, "videoHeight", recording.video_height);
				obs_data_set_int(item, "totalBytes", static_cast<long long>(recording.total_bytes));
				obs_data_set_int(item, "totalFrames", static_cast<long long>(recording.total_frames));
				obs_data_set_string(item, "lastError", recording.last_error.c_str());
				obs_data_array_push_back(items, item);
				obs_data_release(item);
			}
			obs_data_set_array(result, "recordings", items);
			obs_data_array_release(items);
			return result;
		}
	};

	std::vector<Session> sessions;

	Session *find(const std::string &session_id)
	{
		auto item = std::find_if(sessions.begin(), sessions.end(),
					 [session_id](const Session &session) { return session.matches(session_id); });
		return item != sessions.end() ? &*item : nullptr;
	}

	size_t active_recording_count() const
	{
		size_t count = 0;
		for (const auto &session : sessions)
			count += static_cast<size_t>(
				std::count_if(session.recordings.begin(), session.recordings.end(),
					      [](const Recording &recording) { return recording.active(); }));
		return count;
	}

	bool scene_active(const std::string &scene_name) const
	{
		for (const auto &session : sessions) {
			if (std::any_of(session.recordings.begin(), session.recordings.end(),
					[&scene_name](const Recording &recording) {
						return recording.scene_name == scene_name && recording.active();
					}))
				return true;
		}
		return false;
	}

	bool active() const
	{
		return std::any_of(sessions.begin(), sessions.end(),
				   [](const Session &session) { return session.active(); });
	}

	void release_inactive()
	{
		for (auto &session : sessions)
			session.release_inactive();
	}

	void prune_history()
	{
		constexpr size_t history_limit = 32;
		while (sessions.size() >= history_limit) {
			auto item = std::find_if(sessions.begin(), sessions.end(),
						 [](const Session &session) { return !session.active(); });
			if (item == sessions.end())
				break;
			item->shutdown();
			sessions.erase(item);
		}
	}

	void shutdown()
	{
		for (auto &session : sessions)
			session.shutdown();
	}
};

SceneRecordingSessionManager::SceneRecordingSessionManager() : impl_(std::make_unique<Impl>()) {}

SceneRecordingSessionManager::~SceneRecordingSessionManager()
{
	impl_->shutdown();
}

obs_data_t *SceneRecordingSessionManager::start(const std::vector<SceneRecordingSpec> &specs)
{
	obs_data_t *result = obs_data_create();
	impl_->release_inactive();
	if (specs.empty() || specs.size() > kMaxSceneRecordings) {
		set_error(result, "OBS_ARGUMENT_INVALID");
		return result;
	}
	if (impl_->active_recording_count() + specs.size() > kMaxSceneRecordings) {
		set_error(result, "OBS_OUTPUT_ACTIVE");
		return result;
	}
	std::set<std::string> scenes;
	std::set<std::string> prefixes;
	std::set<std::string> output_paths;
	for (const auto &spec : specs) {
		std::string folded = spec.file_name_prefix;
		std::transform(folded.begin(), folded.end(), folded.begin(),
			       [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
		const bool has_any_binding = !spec.source_name.empty() || spec.process_id != 0 ||
					     spec.window_handle != 0;
		const bool has_exact_binding = valid_name(spec.source_name, 256) && spec.process_id != 0 &&
					       spec.window_handle != 0;
		if (!valid_name(spec.scene_name, 256) || !valid_prefix(spec.file_name_prefix) ||
		    (!spec.file_name.empty() && !valid_file_name(spec.file_name)) ||
		    !valid_optional_id(spec.application_id) || !valid_optional_id(spec.run_id) ||
		    !valid_optional_path(spec.output_directory) || (has_any_binding && !has_exact_binding) ||
		    !scenes.insert(spec.scene_name).second || !prefixes.insert(folded).second) {
			set_error(result, "OBS_ARGUMENT_INVALID");
			return result;
		}
		if (impl_->scene_active(spec.scene_name)) {
			set_error(result, "OBS_OUTPUT_ACTIVE");
			return result;
		}
	}
	obs_video_info base_video_info{};
	if (!obs_get_video_info(&base_video_info) || base_video_info.output_width == 0 ||
	    base_video_info.output_height == 0) {
		set_error(result, "OBS_INSTANCE_NOT_READY");
		return result;
	}
	obs_audio_info base_audio_info{};
	if (!obs_get_audio_info(&base_audio_info) || base_audio_info.samples_per_sec == 0 ||
	    base_audio_info.speakers == SPEAKERS_UNKNOWN) {
		set_error(result, "OBS_INSTANCE_NOT_READY");
		return result;
	}

	Impl::Session session;
	const QDateTime started = QDateTime::currentDateTime();
	session.started_at = started.toString(Qt::ISODate).toStdString();
	session.session_id = QUuid::createUuid().toString(QUuid::WithoutBraces).toStdString();
	const QString timestamp = started.toString(QStringLiteral("yyyy-MM-dd HH-mm-ss"));
	for (size_t index = 0; index < specs.size(); ++index) {
		const auto &spec = specs[index];
		Impl::Recording recording;
		recording.scene_name = spec.scene_name;
		recording.application_id = spec.application_id;
		recording.run_id = spec.run_id;
		QString directory = spec.output_directory.empty()
					    ? recording_directory()
					    : QString::fromUtf8(spec.output_directory.c_str()).trimmed();
		if (directory.isEmpty() || (!spec.output_directory.empty() && !QFileInfo(directory).isAbsolute()) ||
		    directory.size() > 4096 || (!QDir(directory).exists() && !QDir().mkpath(directory))) {
			set_error(result,
				  spec.output_directory.empty() ? "OBS_REQUEST_FAILED" : "OBS_ARGUMENT_INVALID");
			session.shutdown();
			return result;
		}
		directory = QDir::cleanPath(directory);
		recording.output_directory = QDir::toNativeSeparators(directory).toStdString();
		recording.file_name = spec.file_name.empty()
					      ? (QString::fromUtf8(spec.file_name_prefix.c_str()) +
						 QStringLiteral(" ") + timestamp + QStringLiteral(".mp4"))
							.toStdString()
					      : spec.file_name;
		const QString output_path = QDir(directory).filePath(QString::fromUtf8(recording.file_name.c_str()));
		recording.output_path = QDir::toNativeSeparators(output_path).toStdString();
		if (QFileInfo::exists(output_path) || !output_paths.insert(recording.output_path).second) {
			set_error(result, "OBS_OUTPUT_ACTIVE");
			session.shutdown();
			return result;
		}
		recording.scene = obs_get_source_by_name(spec.scene_name.c_str());
		if (recording.scene == nullptr || obs_source_get_type(recording.scene) != OBS_SOURCE_TYPE_SCENE) {
			if (recording.scene != nullptr)
				obs_source_release(recording.scene);
			set_error(result, "OBS_SCENE_NOT_FOUND");
			session.shutdown();
			return result;
		}
		obs_scene_t *source_scene = obs_scene_from_source(recording.scene);
		SceneSources sources;
		if (source_scene != nullptr)
			obs_scene_enum_items(source_scene, collect_scene_sources, &sources);
		if (sources.capture == nullptr || sources.ambiguous_capture || sources.ambiguous_agent_overlay ||
		    sources.ambiguous_presentation_overlay) {
			set_error(result, sources.ambiguous_capture || sources.ambiguous_agent_overlay ||
							  sources.ambiguous_presentation_overlay
						  ? "OBS_TARGET_AMBIGUOUS"
						  : "OBS_SOURCE_NOT_FOUND");
			recording.release(true);
			session.shutdown();
			return result;
		}
		const char *capture_source_name = obs_source_get_name(sources.capture);
		recording.source_name = capture_source_name != nullptr ? capture_source_name : "";
		obs_data_t *capture_settings = obs_source_get_settings(sources.capture);
		if (capture_settings != nullptr) {
			recording.process_id =
				static_cast<uint32_t>(obs_data_get_int(capture_settings, "_dcc_process_id"));
			recording.window_handle =
				static_cast<uint64_t>(obs_data_get_int(capture_settings, "_dcc_window_handle"));
			obs_data_release(capture_settings);
		}
		if (!spec.source_name.empty()) {
			recording.binding_verified = recording.source_name == spec.source_name &&
						     recording.process_id == spec.process_id &&
						     recording.window_handle == spec.window_handle &&
						     exact_window_is_live(spec.process_id, spec.window_handle);
			if (!recording.binding_verified) {
				set_error(result, "OBS_WINDOW_IDENTITY_DRIFT");
				recording.release(true);
				session.shutdown();
				return result;
			}
		}
		if (!spec.source_name.empty()) {
			exact_window_client_size(spec.process_id, spec.window_handle, recording.video_width,
						 recording.video_height);
		} else {
			recording.video_width = obs_source_get_width(sources.capture);
			recording.video_height = obs_source_get_height(sources.capture);
		}
		recording.video_width = encoder_compatible_dimension(recording.video_width);
		recording.video_height = encoder_compatible_dimension(recording.video_height);
		if (recording.video_width == 0 || recording.video_height == 0) {
			set_error(result, "OBS_INSTANCE_NOT_READY");
			recording.release(true);
			session.shutdown();
			return result;
		}
		recording.recording_scene = obs_scene_create_private(
			("dcc-mcp-recording-scene-" + session.session_id + "-" + std::to_string(index + 1)).c_str());
		bool capture_added = false;
		bool agent_overlay_added = sources.agent_overlay == nullptr;
		bool presentation_overlay_added = sources.presentation_overlay == nullptr;
		if (recording.recording_scene != nullptr) {
			obs_sceneitem_t *capture_item = obs_scene_add(recording.recording_scene, sources.capture);
			if (capture_item != nullptr) {
				capture_added = true;
				vec2 origin{0.0f, 0.0f};
				vec2 native_scale{1.0f, 1.0f};
				obs_sceneitem_set_alignment(capture_item, OBS_ALIGN_LEFT | OBS_ALIGN_TOP);
				obs_sceneitem_set_pos(capture_item, &origin);
				obs_sceneitem_set_scale(capture_item, &native_scale);
			}
			if (sources.presentation_overlay != nullptr) {
				obs_sceneitem_t *overlay_item =
					obs_scene_add(recording.recording_scene, sources.presentation_overlay);
				if (overlay_item != nullptr) {
					presentation_overlay_added = true;
					position_overlay(overlay_item, sources.presentation_overlay,
							 recording.video_width, recording.video_height, 0.48f, 0.62f);
				}
			}
			if (sources.agent_overlay != nullptr) {
				obs_sceneitem_t *overlay_item =
					obs_scene_add(recording.recording_scene, sources.agent_overlay);
				if (overlay_item != nullptr) {
					agent_overlay_added = true;
					position_overlay(overlay_item, sources.agent_overlay, recording.video_width,
							 recording.video_height, 0.40f, 0.18f);
				}
			}
		}
		if (!capture_added || !agent_overlay_added || !presentation_overlay_added) {
			recording.release(true);
			set_error(result, "OBS_REQUEST_FAILED");
			session.shutdown();
			return result;
		}
		obs_video_info video_info = base_video_info;
		video_info.base_width = recording.video_width;
		video_info.base_height = recording.video_height;
		video_info.output_width = recording.video_width;
		video_info.output_height = recording.video_height;
		recording.view = obs_view_create();
		if (recording.view != nullptr) {
			obs_view_set_source(recording.view, 0,
					    recording.recording_scene != nullptr
						    ? obs_scene_get_source(recording.recording_scene)
						    : nullptr);
			recording.video = obs_view_add2(recording.view, &video_info);
		}
		obs_data_t *video_settings = encoder_settings();
		const std::string suffix = session.session_id + "-" + std::to_string(index + 1);
		recording.encoder = obs_video_encoder_create("obs_x264", ("dcc-mcp-scene-encoder-" + suffix).c_str(),
							     video_settings, nullptr);
		obs_data_release(video_settings);
		if (recording.encoder != nullptr && recording.video != nullptr)
			obs_encoder_set_video(recording.encoder, recording.video);
		audio_output_info silent_audio_info{};
		silent_audio_info.name = "dcc-mcp-scene-silent-audio";
		silent_audio_info.samples_per_sec = base_audio_info.samples_per_sec;
		silent_audio_info.format = AUDIO_FORMAT_FLOAT_PLANAR;
		silent_audio_info.speakers = base_audio_info.speakers;
		silent_audio_info.input_callback = silent_audio_input;
		if (audio_output_open(&recording.silent_audio, &silent_audio_info) == AUDIO_OUTPUT_SUCCESS) {
			obs_data_t *audio_settings = obs_data_create();
			obs_data_set_int(audio_settings, "bitrate", 64);
			recording.silent_audio_encoder = obs_audio_encoder_create(
				"ffmpeg_aac", ("dcc-mcp-scene-silent-audio-encoder-" + suffix).c_str(), audio_settings,
				0, nullptr);
			obs_data_release(audio_settings);
		}
		if (recording.silent_audio_encoder != nullptr && recording.silent_audio != nullptr)
			obs_encoder_set_audio(recording.silent_audio_encoder, recording.silent_audio);
		obs_data_t *output_settings = obs_data_create();
		obs_data_set_string(output_settings, "path", recording.output_path.c_str());
		recording.output = obs_output_create("mp4_output", ("dcc-mcp-scene-output-" + suffix).c_str(),
						     output_settings, nullptr);
		obs_data_release(output_settings);
		if (recording.output != nullptr && recording.encoder != nullptr)
			obs_output_set_video_encoder(recording.output, recording.encoder);
		if (recording.output != nullptr && recording.silent_audio_encoder != nullptr)
			obs_output_set_audio_encoder(recording.output, recording.silent_audio_encoder, 0);
		if (recording.view == nullptr || recording.video == nullptr || recording.encoder == nullptr ||
		    recording.silent_audio == nullptr || recording.silent_audio_encoder == nullptr ||
		    recording.output == nullptr) {
			recording.release(true);
			set_error(result, "OBS_REQUEST_FAILED");
			session.shutdown();
			return result;
		}
		session.recordings.push_back(std::move(recording));
	}

	for (auto &recording : session.recordings) {
		if (!obs_output_start(recording.output)) {
			const char *error = obs_output_get_last_error(recording.output);
			recording.last_error = error != nullptr ? error : "output start failed";
			for (auto &rollback : session.recordings) {
				rollback.release(true);
				QFile::remove(QString::fromUtf8(rollback.output_path.c_str()));
			}
			set_error(result, "OBS_REQUEST_FAILED");
			return result;
		}
	}
	const std::string session_id = session.session_id;
	impl_->prune_history();
	impl_->sessions.push_back(std::move(session));
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_string(result, "sessionId", session_id.c_str());
	return result;
}

obs_data_t *SceneRecordingSessionManager::status(const std::string &session_id)
{
	auto *session = impl_->find(session_id);
	if (session == nullptr) {
		obs_data_t *result = obs_data_create();
		set_error(result, "OBS_OUTPUT_NOT_FOUND");
		return result;
	}
	return session->status_data();
}

obs_data_t *SceneRecordingSessionManager::stop(const std::string &session_id)
{
	obs_data_t *result = obs_data_create();
	auto *session = impl_->find(session_id);
	if (session == nullptr) {
		set_error(result, "OBS_OUTPUT_NOT_FOUND");
		return result;
	}
	for (auto &recording : session->recordings) {
		if (!recording.active())
			continue;
		if (recording.stop_requested)
			obs_output_force_stop(recording.output);
		else {
			recording.stop_requested = true;
			obs_output_stop(recording.output);
		}
	}
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_string(result, "sessionId", session->session_id.c_str());
	return result;
}

bool SceneRecordingSessionManager::active() const
{
	return impl_->active();
}

void SceneRecordingSessionManager::shutdown()
{
	impl_->shutdown();
}

} // namespace dcc_mcp_obs
