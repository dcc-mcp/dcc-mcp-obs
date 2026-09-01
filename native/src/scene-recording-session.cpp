#include "scene-recording-session.hpp"
#include "agent-input-overlay.hpp"

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

struct SceneSources {
	obs_source_t *capture = nullptr;
	obs_source_t *overlay = nullptr;
	bool ambiguous_capture = false;
	bool ambiguous_overlay = false;
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
		if (sources->overlay != nullptr)
			sources->ambiguous_overlay = true;
		else
			sources->overlay = source;
	}
	return true;
}

void position_overlay(obs_sceneitem_t *item, obs_source_t *source, uint32_t width, uint32_t height)
{
	obs_data_t *settings = obs_source_get_settings(source);
	const std::string anchor = obs_data_get_string(settings, "anchor");
	const float margin = static_cast<float>(std::clamp<long long>(obs_data_get_int(settings, "margin"), 8, 160));
	obs_data_release(settings);
	const float source_width = static_cast<float>(std::max<uint32_t>(1, obs_source_get_width(source)));
	const float source_height = static_cast<float>(std::max<uint32_t>(1, obs_source_get_height(source)));
	const float scale_value = std::min(1.0f, std::min(static_cast<float>(width) * 0.40f / source_width,
							  static_cast<float>(height) * 0.18f / source_height));
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
		std::string file_name;
		std::string output_path;
		obs_source_t *scene = nullptr;
		obs_scene_t *recording_scene = nullptr;
		obs_view_t *view = nullptr;
		video_t *video = nullptr;
		obs_encoder_t *encoder = nullptr;
		obs_output_t *output = nullptr;
		uint64_t total_bytes = 0;
		uint64_t total_frames = 0;
		std::string last_error;
		uint32_t video_width = 0;
		uint32_t video_height = 0;

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

	std::string session_id;
	std::string started_at;
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
	}

	void shutdown()
	{
		for (auto &recording : recordings)
			recording.release(true);
	}

	obs_data_t *status_data()
	{
		release_inactive();
		obs_data_t *result = obs_data_create();
		obs_data_set_string(result, "sessionId", session_id.c_str());
		obs_data_set_bool(result, "sessionActive", active());
		obs_data_set_string(result, "startedAt", started_at.c_str());
		obs_data_array_t *items = obs_data_array_create();
		for (auto &recording : recordings) {
			recording.capture_status();
			obs_data_t *item = obs_data_create();
			obs_data_set_string(item, "sceneName", recording.scene_name.c_str());
			obs_data_set_string(item, "fileName", recording.file_name.c_str());
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

SceneRecordingSessionManager::SceneRecordingSessionManager() : impl_(std::make_unique<Impl>()) {}

SceneRecordingSessionManager::~SceneRecordingSessionManager()
{
	impl_->shutdown();
}

obs_data_t *SceneRecordingSessionManager::start(const std::vector<SceneRecordingSpec> &specs)
{
	obs_data_t *result = obs_data_create();
	if (impl_->active()) {
		set_error(result, "OBS_OUTPUT_ACTIVE");
		return result;
	}
	if (specs.empty() || specs.size() > kMaxSceneRecordings) {
		set_error(result, "OBS_ARGUMENT_INVALID");
		return result;
	}
	std::set<std::string> scenes;
	std::set<std::string> prefixes;
	for (const auto &spec : specs) {
		std::string folded = spec.file_name_prefix;
		std::transform(folded.begin(), folded.end(), folded.begin(),
			       [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
		if (!valid_name(spec.scene_name, 256) || !valid_prefix(spec.file_name_prefix) ||
		    !scenes.insert(spec.scene_name).second || !prefixes.insert(folded).second) {
			set_error(result, "OBS_ARGUMENT_INVALID");
			return result;
		}
	}

	const QString directory = recording_directory();
	if (directory.isEmpty() || (!QDir(directory).exists() && !QDir().mkpath(directory))) {
		set_error(result, "OBS_REQUEST_FAILED");
		return result;
	}
	obs_video_info base_video_info{};
	if (!obs_get_video_info(&base_video_info) || base_video_info.output_width == 0 ||
	    base_video_info.output_height == 0) {
		set_error(result, "OBS_INSTANCE_NOT_READY");
		return result;
	}

	impl_->shutdown();
	impl_->recordings.clear();
	const QDateTime started = QDateTime::currentDateTime();
	impl_->started_at = started.toString(Qt::ISODate).toStdString();
	impl_->session_id = QUuid::createUuid().toString(QUuid::WithoutBraces).toStdString();
	const QString timestamp = started.toString(QStringLiteral("yyyy-MM-dd HH-mm-ss"));
	for (size_t index = 0; index < specs.size(); ++index) {
		const auto &spec = specs[index];
		Impl::Recording recording;
		recording.scene_name = spec.scene_name;
		recording.file_name = (QString::fromUtf8(spec.file_name_prefix.c_str()) + QStringLiteral(" ") +
				       timestamp + QStringLiteral(".mp4"))
					      .toStdString();
		const QString output_path = QDir(directory).filePath(QString::fromUtf8(recording.file_name.c_str()));
		if (QFileInfo::exists(output_path)) {
			set_error(result, "OBS_OUTPUT_ACTIVE");
			impl_->shutdown();
			impl_->recordings.clear();
			return result;
		}
		recording.output_path = QDir::toNativeSeparators(output_path).toStdString();
		recording.scene = obs_get_source_by_name(spec.scene_name.c_str());
		if (recording.scene == nullptr || obs_source_get_type(recording.scene) != OBS_SOURCE_TYPE_SCENE) {
			if (recording.scene != nullptr)
				obs_source_release(recording.scene);
			set_error(result, "OBS_SCENE_NOT_FOUND");
			impl_->shutdown();
			impl_->recordings.clear();
			return result;
		}
		obs_scene_t *source_scene = obs_scene_from_source(recording.scene);
		SceneSources sources;
		if (source_scene != nullptr)
			obs_scene_enum_items(source_scene, collect_scene_sources, &sources);
		if (sources.capture == nullptr || sources.ambiguous_capture || sources.ambiguous_overlay) {
			set_error(result, sources.ambiguous_capture || sources.ambiguous_overlay
						  ? "OBS_TARGET_AMBIGUOUS"
						  : "OBS_SOURCE_NOT_FOUND");
			recording.release(true);
			impl_->shutdown();
			impl_->recordings.clear();
			return result;
		}
		recording.video_width = obs_source_get_width(sources.capture);
		recording.video_height = obs_source_get_height(sources.capture);
		if (recording.video_width == 0 || recording.video_height == 0) {
			set_error(result, "OBS_INSTANCE_NOT_READY");
			recording.release(true);
			impl_->shutdown();
			impl_->recordings.clear();
			return result;
		}
		recording.recording_scene = obs_scene_create_private(
			("dcc-mcp-recording-scene-" + impl_->session_id + "-" + std::to_string(index + 1)).c_str());
		bool capture_added = false;
		bool overlay_added = sources.overlay == nullptr;
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
			if (sources.overlay != nullptr) {
				obs_sceneitem_t *overlay_item =
					obs_scene_add(recording.recording_scene, sources.overlay);
				if (overlay_item != nullptr) {
					overlay_added = true;
					position_overlay(overlay_item, sources.overlay, recording.video_width,
							 recording.video_height);
				}
			}
		}
		if (!capture_added || !overlay_added) {
			recording.release(true);
			set_error(result, "OBS_REQUEST_FAILED");
			impl_->shutdown();
			impl_->recordings.clear();
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
		const std::string suffix = impl_->session_id + "-" + std::to_string(index + 1);
		recording.encoder = obs_video_encoder_create("obs_x264", ("dcc-mcp-scene-encoder-" + suffix).c_str(),
							     video_settings, nullptr);
		obs_data_release(video_settings);
		if (recording.encoder != nullptr && recording.video != nullptr)
			obs_encoder_set_video(recording.encoder, recording.video);
		obs_data_t *output_settings = obs_data_create();
		obs_data_set_string(output_settings, "path", recording.output_path.c_str());
		recording.output = obs_output_create("mp4_output", ("dcc-mcp-scene-output-" + suffix).c_str(),
						     output_settings, nullptr);
		obs_data_release(output_settings);
		if (recording.output != nullptr && recording.encoder != nullptr)
			obs_output_set_video_encoder(recording.output, recording.encoder);
		if (recording.view == nullptr || recording.video == nullptr || recording.encoder == nullptr ||
		    recording.output == nullptr) {
			recording.release(true);
			set_error(result, "OBS_REQUEST_FAILED");
			impl_->shutdown();
			impl_->recordings.clear();
			return result;
		}
		impl_->recordings.push_back(std::move(recording));
	}

	for (auto &recording : impl_->recordings) {
		if (!obs_output_start(recording.output)) {
			const char *error = obs_output_get_last_error(recording.output);
			recording.last_error = error != nullptr ? error : "output start failed";
			for (auto &rollback : impl_->recordings) {
				rollback.release(true);
				QFile::remove(QString::fromUtf8(rollback.output_path.c_str()));
			}
			set_error(result, "OBS_REQUEST_FAILED");
			impl_->recordings.clear();
			return result;
		}
	}
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_string(result, "sessionId", impl_->session_id.c_str());
	return result;
}

obs_data_t *SceneRecordingSessionManager::status(const std::string &session_id)
{
	if (!impl_->matches(session_id)) {
		obs_data_t *result = obs_data_create();
		set_error(result, "OBS_OUTPUT_NOT_FOUND");
		return result;
	}
	return impl_->status_data();
}

obs_data_t *SceneRecordingSessionManager::stop(const std::string &session_id)
{
	obs_data_t *result = obs_data_create();
	if (!impl_->matches(session_id)) {
		set_error(result, "OBS_OUTPUT_NOT_FOUND");
		return result;
	}
	for (auto &recording : impl_->recordings) {
		if (recording.active())
			obs_output_stop(recording.output);
	}
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_string(result, "sessionId", impl_->session_id.c_str());
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
