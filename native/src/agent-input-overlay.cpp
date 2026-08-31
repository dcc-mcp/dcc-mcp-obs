#include "agent-input-overlay.hpp"

#include <obs-frontend-api.h>
#include <obs-module.h>
#include <util/platform.h>

#include <QColor>
#include <QFont>
#include <QImage>
#include <QPainter>
#include <QPainterPath>
#include <QString>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace dcc_mcp_obs {
namespace {

constexpr uint32_t kOverlayWidth = 620;
constexpr uint32_t kOverlayHeight = 104;
constexpr float kOverlayMargin = 48.0f;

struct AgentInputOverlaySource {
	std::mutex mutex;
	QImage image;
	uint64_t image_revision = 1;
	uint64_t texture_revision = 0;
	gs_texture_t *texture = nullptr;
	int64_t expires_at_ms = 0;
	bool active = false;
};

int64_t epoch_ms()
{
	return std::chrono::duration_cast<std::chrono::milliseconds>(
		       std::chrono::system_clock::now().time_since_epoch())
		.count();
}

void set_error(obs_data_t *data, const char *code)
{
	obs_data_set_bool(data, "ok", false);
	obs_data_set_string(data, "errorCode", code);
}

QString display_kind(const std::string &kind)
{
	if (kind == "shortcut")
		return QStringLiteral("KEYBOARD");
	if (kind == "mouse_button")
		return QStringLiteral("MOUSE");
	if (kind == "mouse_wheel")
		return QStringLiteral("WHEEL");
	if (kind == "typing")
		return QStringLiteral("TYPING");
	return QStringLiteral("READY");
}

QImage render_overlay_image(obs_data_t *settings)
{
	QImage image(static_cast<int>(kOverlayWidth), static_cast<int>(kOverlayHeight), QImage::Format_RGBA8888);
	image.fill(Qt::transparent);
	QPainter painter(&image);
	painter.setRenderHint(QPainter::Antialiasing, true);
	painter.setRenderHint(QPainter::TextAntialiasing, true);

	QPainterPath background;
	background.addRoundedRect(QRectF(1.0, 1.0, kOverlayWidth - 2.0, kOverlayHeight - 2.0), 18.0, 18.0);
	painter.fillPath(background, QColor(14, 19, 27, 238));
	painter.setPen(QPen(QColor(59, 215, 191, 210), 2.0));
	painter.drawPath(background);

	QPainterPath badge;
	badge.addRoundedRect(QRectF(14.0, 14.0, 106.0, 76.0), 13.0, 13.0);
	painter.fillPath(badge, QColor(28, 39, 51, 245));
	painter.setPen(Qt::NoPen);
	painter.fillRect(QRectF(14.0, 14.0, 5.0, 76.0), QColor(59, 215, 191));

	QFont badge_small(QStringLiteral("Inter"), 9, QFont::DemiBold);
	badge_small.setLetterSpacing(QFont::AbsoluteSpacing, 1.8);
	painter.setFont(badge_small);
	painter.setPen(QColor(139, 154, 173));
	painter.drawText(QRectF(31.0, 25.0, 76.0, 20.0), Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("DCC-MCP"));
	QFont badge_large(QStringLiteral("Inter"), 15, QFont::Bold);
	painter.setFont(badge_large);
	painter.setPen(QColor(245, 248, 252));
	painter.drawText(QRectF(31.0, 45.0, 76.0, 28.0), Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("AGENT"));

	const std::string kind = obs_data_get_string(settings, "eventKind");
	const QString cue = QString::fromUtf8(obs_data_get_string(settings, "cueLabel"));
	QFont kind_font(QStringLiteral("Inter"), 9, QFont::DemiBold);
	kind_font.setLetterSpacing(QFont::AbsoluteSpacing, 1.6);
	painter.setFont(kind_font);
	painter.setPen(QColor(59, 215, 191));
	painter.drawText(QRectF(145.0, 22.0, 440.0, 18.0), Qt::AlignLeft | Qt::AlignVCenter, display_kind(kind));

	QFont cue_font(QStringLiteral("Inter"), cue.size() > 28 ? 18 : 23, QFont::DemiBold);
	painter.setFont(cue_font);
	painter.setPen(QColor(245, 248, 252));
	painter.drawText(QRectF(143.0, 42.0, 452.0, 42.0), Qt::AlignLeft | Qt::AlignVCenter,
			 cue.isEmpty() ? QStringLiteral("INPUT READY") : cue);
	return image;
}

const char *source_name(void *)
{
	return "DCC-MCP Agent Input Overlay";
}

void source_defaults(obs_data_t *settings)
{
	obs_data_set_default_bool(settings, "active", false);
	obs_data_set_default_int(settings, "activitySequence", 0);
	obs_data_set_default_string(settings, "eventKind", "none");
	obs_data_set_default_string(settings, "keysCsv", "");
	obs_data_set_default_string(settings, "mouseButton", "none");
	obs_data_set_default_string(settings, "wheelDirection", "none");
	obs_data_set_default_int(settings, "characterCount", 0);
	obs_data_set_default_string(settings, "cueLabel", "");
	obs_data_set_default_int(settings, "durationMs", 0);
	obs_data_set_default_int(settings, "expiresAtMs", 0);
	obs_data_set_default_string(settings, "theme", "dcc_mcp_dark");
}

void source_update(void *data, obs_data_t *settings)
{
	auto *source = static_cast<AgentInputOverlaySource *>(data);
	QImage image = render_overlay_image(settings);
	std::lock_guard<std::mutex> lock(source->mutex);
	source->image = std::move(image);
	source->active = obs_data_get_bool(settings, "active");
	source->expires_at_ms = obs_data_get_int(settings, "expiresAtMs");
	++source->image_revision;
}

void *source_create(obs_data_t *settings, obs_source_t *)
{
	auto *source = new AgentInputOverlaySource();
	source_update(source, settings);
	return source;
}

void source_destroy(void *data)
{
	auto *source = static_cast<AgentInputOverlaySource *>(data);
	obs_enter_graphics();
	if (source->texture != nullptr)
		gs_texture_destroy(source->texture);
	obs_leave_graphics();
	delete source;
}

uint32_t source_width(void *)
{
	return kOverlayWidth;
}

uint32_t source_height(void *)
{
	return kOverlayHeight;
}

void source_render(void *data, gs_effect_t *)
{
	auto *source = static_cast<AgentInputOverlaySource *>(data);
	QImage image;
	uint64_t image_revision = 0;
	{
		std::lock_guard<std::mutex> lock(source->mutex);
		if (!source->active || source->expires_at_ms <= epoch_ms())
			return;
		image = source->image;
		image_revision = source->image_revision;
	}
	if (source->texture_revision != image_revision) {
		if (source->texture != nullptr)
			gs_texture_destroy(source->texture);
		const uint8_t *planes[] = {image.constBits()};
		source->texture = gs_texture_create(kOverlayWidth, kOverlayHeight, GS_RGBA, 1, planes, GS_DYNAMIC);
		source->texture_revision = image_revision;
	}
	if (source->texture != nullptr)
		obs_source_draw(source->texture, 0, 0, 0, 0, false);
}

obs_source_info overlay_source_info()
{
	obs_source_info info{};
	info.id = kAgentInputOverlaySourceId;
	info.type = OBS_SOURCE_TYPE_INPUT;
	info.output_flags = OBS_SOURCE_VIDEO;
	info.get_name = source_name;
	info.create = source_create;
	info.destroy = source_destroy;
	info.get_defaults = source_defaults;
	info.update = source_update;
	info.get_width = source_width;
	info.get_height = source_height;
	info.video_render = source_render;
	return info;
}

obs_source_t *scene_source(const std::string &scene_name)
{
	obs_source_t *source = obs_get_source_by_name(scene_name.c_str());
	if (source != nullptr && obs_source_get_type(source) != OBS_SOURCE_TYPE_SCENE) {
		obs_source_release(source);
		return nullptr;
	}
	return source;
}

obs_sceneitem_t *overlay_item(obs_scene_t *scene, const std::string &source_name_value)
{
	obs_sceneitem_t *item = obs_scene_find_source(scene, source_name_value.c_str());
	if (item == nullptr)
		return nullptr;
	obs_source_t *source = obs_sceneitem_get_source(item);
	return source != nullptr && std::string(obs_source_get_id(source)) == kAgentInputOverlaySourceId ? item
													 : nullptr;
}

std::string anchor_for_item(obs_sceneitem_t *item)
{
	obs_video_info video{};
	vec2 position{};
	obs_sceneitem_get_pos(item, &position);
	if (!obs_get_video_info(&video))
		return "custom";
	const float bottom = std::max(0.0f, static_cast<float>(video.base_height) - kOverlayHeight - kOverlayMargin);
	const float left = kOverlayMargin;
	const float center = std::max(0.0f, (static_cast<float>(video.base_width) - kOverlayWidth) / 2.0f);
	const float right = std::max(0.0f, static_cast<float>(video.base_width) - kOverlayWidth - kOverlayMargin);
	if (std::fabs(position.y - bottom) > 1.0f)
		return "custom";
	if (std::fabs(position.x - left) <= 1.0f)
		return "bottom_left";
	if (std::fabs(position.x - center) <= 1.0f)
		return "bottom_center";
	if (std::fabs(position.x - right) <= 1.0f)
		return "bottom_right";
	return "custom";
}

void position_item(obs_sceneitem_t *item, const std::string &anchor, const obs_video_info &video)
{
	const float bottom = std::max(0.0f, static_cast<float>(video.base_height) - kOverlayHeight - kOverlayMargin);
	float x = kOverlayMargin;
	if (anchor == "bottom_center")
		x = std::max(0.0f, (static_cast<float>(video.base_width) - kOverlayWidth) / 2.0f);
	else if (anchor == "bottom_right")
		x = std::max(0.0f, static_cast<float>(video.base_width) - kOverlayWidth - kOverlayMargin);
	vec2 position{x, bottom};
	vec2 scale{1.0f, 1.0f};
	obs_sceneitem_set_alignment(item, OBS_ALIGN_LEFT | OBS_ALIGN_TOP);
	obs_sceneitem_set_pos(item, &position);
	obs_sceneitem_set_scale(item, &scale);
	obs_sceneitem_set_order(item, OBS_ORDER_MOVE_TOP);
}

std::string cue_label(const AgentInputActivity &activity)
{
	if (activity.event_kind == "shortcut") {
		std::string label;
		for (const auto &key : activity.keys) {
			if (!label.empty())
				label += " + ";
			std::string upper = key;
			std::transform(upper.begin(), upper.end(), upper.begin(),
				       [](unsigned char value) { return static_cast<char>(std::toupper(value)); });
			label += upper;
		}
		return label;
	}
	if (activity.event_kind == "mouse_button") {
		if (activity.mouse_button == "left")
			return "LMB";
		if (activity.mouse_button == "right")
			return "RMB";
		if (activity.mouse_button == "middle")
			return "MMB";
		return activity.mouse_button == "back" ? "MOUSE 4" : "MOUSE 5";
	}
	if (activity.event_kind == "mouse_wheel") {
		if (activity.wheel_direction == "up")
			return "WHEEL UP";
		if (activity.wheel_direction == "down")
			return "WHEEL DOWN";
		if (activity.wheel_direction == "left")
			return "WHEEL LEFT";
		return "WHEEL RIGHT";
	}
	return "TYPING · " + std::to_string(activity.character_count);
}

bool resolve_binding(const std::string &scene_name, const std::string &source_name_value,
		     obs_source_t **scene_source_value, obs_sceneitem_t **item_value, obs_source_t **overlay_source)
{
	*scene_source_value = scene_source(scene_name);
	if (*scene_source_value == nullptr)
		return false;
	obs_scene_t *scene = obs_scene_from_source(*scene_source_value);
	*item_value = scene != nullptr ? overlay_item(scene, source_name_value) : nullptr;
	if (*item_value == nullptr) {
		obs_source_release(*scene_source_value);
		*scene_source_value = nullptr;
		return false;
	}
	*overlay_source = obs_sceneitem_get_source(*item_value);
	return *overlay_source != nullptr;
}

void reset_activity_settings(obs_data_t *settings, uint64_t sequence)
{
	obs_data_set_bool(settings, "active", false);
	obs_data_set_int(settings, "activitySequence", static_cast<long long>(sequence));
	obs_data_set_string(settings, "eventKind", "none");
	obs_data_set_string(settings, "keysCsv", "");
	obs_data_set_string(settings, "mouseButton", "none");
	obs_data_set_string(settings, "wheelDirection", "none");
	obs_data_set_int(settings, "characterCount", 0);
	obs_data_set_string(settings, "cueLabel", "");
	obs_data_set_int(settings, "durationMs", 0);
	obs_data_set_int(settings, "expiresAtMs", 0);
	obs_data_set_string(settings, "theme", "dcc_mcp_dark");
}

} // namespace

void register_agent_input_overlay_source()
{
	static obs_source_info info = overlay_source_info();
	obs_register_source(&info);
}

obs_data_t *create_agent_input_overlay(const std::string &scene_name, const std::string &source_name_value,
				       const std::string &anchor)
{
	obs_data_t *result = obs_data_create();
	obs_source_t *scene_source_value = scene_source(scene_name);
	if (scene_source_value == nullptr) {
		set_error(result, "OBS_SCENE_NOT_FOUND");
		return result;
	}
	obs_video_info video{};
	if (!obs_get_video_info(&video) || video.base_width == 0 || video.base_height == 0) {
		set_error(result, "OBS_INSTANCE_NOT_READY");
		obs_source_release(scene_source_value);
		return result;
	}
	obs_scene_t *scene = obs_scene_from_source(scene_source_value);
	obs_sceneitem_t *item = scene != nullptr ? overlay_item(scene, source_name_value) : nullptr;
	obs_source_t *overlay = nullptr;
	if (item == nullptr) {
		overlay = obs_get_source_by_name(source_name_value.c_str());
		if (overlay != nullptr && std::string(obs_source_get_id(overlay)) != kAgentInputOverlaySourceId) {
			set_error(result, "OBS_TARGET_AMBIGUOUS");
			obs_source_release(overlay);
			obs_source_release(scene_source_value);
			return result;
		}
		if (overlay == nullptr) {
			obs_data_t *settings = obs_data_create();
			reset_activity_settings(settings, 0);
			overlay = obs_source_create(kAgentInputOverlaySourceId, source_name_value.c_str(), settings,
						    nullptr);
			obs_data_release(settings);
		}
		if (overlay == nullptr)
			set_error(result, "OBS_REQUEST_FAILED");
		else
			item = obs_scene_add(scene, overlay);
	}
	if (item == nullptr && !obs_data_has_user_value(result, "ok"))
		set_error(result, "OBS_REQUEST_FAILED");
	if (item != nullptr) {
		position_item(item, anchor, video);
		obs_data_set_bool(result, "accepted", true);
	}
	if (overlay != nullptr)
		obs_source_release(overlay);
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *get_agent_input_overlay(const std::string &scene_name, const std::string &source_name_value)
{
	obs_data_t *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	obs_data_t *settings = obs_source_get_settings(overlay);
	const int64_t expires_at = obs_data_get_int(settings, "expiresAtMs");
	const int64_t remaining = std::clamp<int64_t>(expires_at - epoch_ms(), 0, 5000);
	const bool active = obs_data_get_bool(settings, "active") && remaining > 0;
	obs_data_set_string(result, "sceneName", scene_name.c_str());
	obs_data_set_int(result, "sceneItemId", obs_sceneitem_get_id(item));
	obs_data_set_string(result, "sourceName", source_name_value.c_str());
	obs_data_set_string(result, "sourceKind", kAgentInputOverlaySourceId);
	obs_data_set_string(result, "theme", "dcc_mcp_dark");
	obs_data_set_string(result, "anchor", anchor_for_item(item).c_str());
	obs_data_set_bool(result, "active", active);
	obs_data_set_int(result, "activitySequence", obs_data_get_int(settings, "activitySequence"));
	obs_data_set_string(result, "eventKind", active ? obs_data_get_string(settings, "eventKind") : "none");
	obs_data_set_string(result, "keysCsv", active ? obs_data_get_string(settings, "keysCsv") : "");
	obs_data_set_string(result, "mouseButton", active ? obs_data_get_string(settings, "mouseButton") : "none");
	obs_data_set_string(result, "wheelDirection",
			    active ? obs_data_get_string(settings, "wheelDirection") : "none");
	obs_data_set_int(result, "characterCount", active ? obs_data_get_int(settings, "characterCount") : 0);
	obs_data_set_string(result, "cueLabel", active ? obs_data_get_string(settings, "cueLabel") : "");
	obs_data_set_int(result, "durationMs", active ? obs_data_get_int(settings, "durationMs") : 0);
	obs_data_set_int(result, "remainingMs", remaining);
	obs_data_release(settings);
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *emit_agent_input_activity(const std::string &scene_name, const std::string &source_name_value,
				      const AgentInputActivity &activity)
{
	obs_data_t *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	obs_data_t *settings = obs_source_get_settings(overlay);
	const uint64_t sequence = static_cast<uint64_t>(obs_data_get_int(settings, "activitySequence")) + 1;
	obs_data_set_bool(settings, "active", true);
	obs_data_set_int(settings, "activitySequence", static_cast<long long>(sequence));
	obs_data_set_string(settings, "eventKind", activity.event_kind.c_str());
	std::string keys_csv;
	for (const auto &key : activity.keys) {
		if (!keys_csv.empty())
			keys_csv += ',';
		keys_csv += key;
	}
	obs_data_set_string(settings, "keysCsv", keys_csv.c_str());
	obs_data_set_string(settings, "mouseButton", activity.mouse_button.c_str());
	obs_data_set_string(settings, "wheelDirection", activity.wheel_direction.c_str());
	obs_data_set_int(settings, "characterCount", activity.character_count);
	obs_data_set_string(settings, "cueLabel", cue_label(activity).c_str());
	obs_data_set_int(settings, "durationMs", activity.duration_ms);
	obs_data_set_int(settings, "expiresAtMs", epoch_ms() + activity.duration_ms);
	obs_data_set_string(settings, "theme", "dcc_mcp_dark");
	obs_source_update(overlay, settings);
	obs_data_release(settings);
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_int(result, "activitySequence", static_cast<long long>(sequence));
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *clear_agent_input_overlay(const std::string &scene_name, const std::string &source_name_value)
{
	obs_data_t *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	obs_data_t *settings = obs_source_get_settings(overlay);
	const uint64_t sequence = static_cast<uint64_t>(obs_data_get_int(settings, "activitySequence")) + 1;
	reset_activity_settings(settings, sequence);
	obs_source_update(overlay, settings);
	obs_data_release(settings);
	obs_data_set_bool(result, "accepted", true);
	obs_data_set_int(result, "activitySequence", static_cast<long long>(sequence));
	obs_source_release(scene_source_value);
	return result;
}

} // namespace dcc_mcp_obs
