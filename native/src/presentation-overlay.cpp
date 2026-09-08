#include "presentation-overlay.hpp"

#include <QColor>
#include <QFileInfo>
#include <QFont>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPainter>
#include <QPainterPath>
#include <QString>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <utility>

namespace dcc_mcp_obs {
namespace {

constexpr int kMinimumWidth = 360;
constexpr int kMaximumWidth = 960;
constexpr int kMinimumOpacity = 20;
constexpr int kMaximumOpacity = 100;
constexpr int kMinimumMargin = 8;
constexpr int kMaximumMargin = 160;
constexpr int kMaximumLogoBytes = 16 * 1024 * 1024;

struct PresentationOverlaySource {
	std::mutex mutex;
	QImage image;
	uint32_t width = 520;
	uint32_t height = 128;
	uint64_t image_revision = 1;
	uint64_t texture_revision = 0;
	gs_texture_t *texture = nullptr;
	bool visible = true;
};

void set_error(obs_data_t *data, const char *code)
{
	obs_data_set_bool(data, "ok", false);
	obs_data_set_string(data, "errorCode", code);
}

QJsonArray parsed_rows(const char *rows_json)
{
	QJsonParseError error{};
	const auto document = QJsonDocument::fromJson(QByteArray(rows_json != nullptr ? rows_json : "[]"), &error);
	return error.error == QJsonParseError::NoError && document.isArray() ? document.array() : QJsonArray{};
}

bool valid_overlay_text(const QString &value, qsizetype maximum)
{
	if (value.size() > maximum)
		return false;
	for (const auto character : value) {
		if (character.unicode() < 32 || character.unicode() == 127)
			return false;
	}
	return true;
}

uint32_t overlay_height(const QJsonArray &rows)
{
	return static_cast<uint32_t>(92 + std::max<qsizetype>(1, rows.size()) * 38 + 18);
}

QImage render_overlay_image(obs_data_t *settings, uint32_t width, uint32_t height)
{
	QImage image(static_cast<int>(width), static_cast<int>(height), QImage::Format_RGBA8888);
	image.fill(Qt::transparent);
	QPainter painter(&image);
	painter.setRenderHint(QPainter::Antialiasing, true);
	painter.setRenderHint(QPainter::TextAntialiasing, true);
	painter.setOpacity(std::clamp(static_cast<double>(obs_data_get_int(settings, "opacity")) / 100.0, 0.2, 1.0));

	QPainterPath background;
	background.addRoundedRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0), 18.0, 18.0);
	painter.fillPath(background, QColor(14, 19, 27, 238));
	painter.setPen(QPen(QColor(216, 164, 76, 220), 2.0));
	painter.drawPath(background);
	painter.fillRect(QRectF(0.0, 18.0, 5.0, height - 36.0), QColor(216, 164, 76));

	const QString logo_path = QString::fromUtf8(obs_data_get_string(settings, "logoPath"));
	QImage logo;
	if (!logo_path.isEmpty())
		logo.load(logo_path);
	const bool has_logo = !logo.isNull();
	const int logo_size = has_logo ? 58 : 0;
	const int content_left = has_logo ? 88 : 24;
	if (has_logo) {
		QPainterPath clip;
		clip.addRoundedRect(QRectF(18.0, 18.0, logo_size, logo_size), 12.0, 12.0);
		painter.save();
		painter.setClipPath(clip);
		painter.drawImage(QRectF(18.0, 18.0, logo_size, logo_size), logo);
		painter.restore();
	}

	QFont eyebrow(QStringLiteral("Inter"), 8, QFont::DemiBold);
	eyebrow.setLetterSpacing(QFont::AbsoluteSpacing, 1.7);
	painter.setFont(eyebrow);
	painter.setPen(QColor(216, 164, 76));
	painter.drawText(QRectF(content_left, 16.0, width - content_left - 18.0, 18.0),
			 Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("DCC-MCP LIVE"));
	QFont title_font(QStringLiteral("Inter"), 17, QFont::Bold);
	painter.setFont(title_font);
	painter.setPen(QColor(245, 248, 252));
	const QString title = QString::fromUtf8(obs_data_get_string(settings, "title"));
	painter.drawText(QRectF(content_left, 35.0, width - content_left - 18.0, 34.0),
			 Qt::AlignLeft | Qt::AlignVCenter,
			 painter.fontMetrics().elidedText(title, Qt::ElideRight, width - content_left - 22));

	const QJsonArray rows = parsed_rows(obs_data_get_string(settings, "rowsJson"));
	QFont label_font(QStringLiteral("Inter"), 10, QFont::DemiBold);
	QFont value_font(QStringLiteral("Inter"), 11, QFont::Bold);
	int y = 84;
	for (const auto entry : rows) {
		const auto row = entry.toObject();
		painter.setFont(label_font);
		painter.setPen(QColor(139, 154, 173));
		painter.drawText(QRectF(24.0, y, width * 0.40, 26.0), Qt::AlignLeft | Qt::AlignVCenter,
				 row.value(QStringLiteral("label")).toString());
		painter.setFont(value_font);
		painter.setPen(QColor(245, 248, 252));
		const QString value = painter.fontMetrics().elidedText(row.value(QStringLiteral("value")).toString(),
								       Qt::ElideRight, static_cast<int>(width * 0.52));
		painter.drawText(QRectF(width * 0.42, y, width * 0.54 - 20.0, 26.0), Qt::AlignRight | Qt::AlignVCenter,
				 value);
		y += 38;
	}
	return image;
}

const char *source_name(void *)
{
	return "DCC-MCP Presentation Overlay";
}

void source_defaults(obs_data_t *settings)
{
	obs_data_set_default_string(settings, "title", "Training Session");
	obs_data_set_default_string(settings, "rowsJson", "[]");
	obs_data_set_default_string(settings, "logoPath", "");
	obs_data_set_default_bool(settings, "visible", true);
	obs_data_set_default_string(settings, "theme", "dcc_mcp_dark");
	obs_data_set_default_string(settings, "anchor", "top_right");
	obs_data_set_default_int(settings, "opacity", 88);
	obs_data_set_default_int(settings, "margin", 48);
	obs_data_set_default_int(settings, "width", 520);
}

void source_update(void *data, obs_data_t *settings)
{
	auto *source = static_cast<PresentationOverlaySource *>(data);
	const auto rows = parsed_rows(obs_data_get_string(settings, "rowsJson"));
	const uint32_t width = static_cast<uint32_t>(
		std::clamp<long long>(obs_data_get_int(settings, "width"), kMinimumWidth, kMaximumWidth));
	const uint32_t height = overlay_height(rows);
	QImage image = render_overlay_image(settings, width, height);
	std::lock_guard<std::mutex> lock(source->mutex);
	source->image = std::move(image);
	source->width = width;
	source->height = height;
	source->visible = obs_data_get_bool(settings, "visible");
	++source->image_revision;
}

void *source_create(obs_data_t *settings, obs_source_t *)
{
	auto *source = new PresentationOverlaySource();
	source_update(source, settings);
	return source;
}

void source_destroy(void *data)
{
	auto *source = static_cast<PresentationOverlaySource *>(data);
	obs_enter_graphics();
	if (source->texture != nullptr)
		gs_texture_destroy(source->texture);
	obs_leave_graphics();
	delete source;
}

uint32_t source_width(void *data)
{
	auto *source = static_cast<PresentationOverlaySource *>(data);
	std::lock_guard<std::mutex> lock(source->mutex);
	return source->width;
}

uint32_t source_height(void *data)
{
	auto *source = static_cast<PresentationOverlaySource *>(data);
	std::lock_guard<std::mutex> lock(source->mutex);
	return source->height;
}

void source_render(void *data, gs_effect_t *)
{
	auto *source = static_cast<PresentationOverlaySource *>(data);
	QImage image;
	uint64_t revision = 0;
	bool visible = false;
	{
		std::lock_guard<std::mutex> lock(source->mutex);
		image = source->image;
		revision = source->image_revision;
		visible = source->visible;
	}
	if (!visible || image.isNull())
		return;
	if (source->texture_revision != revision) {
		if (source->texture != nullptr)
			gs_texture_destroy(source->texture);
		const uint8_t *planes[] = {image.constBits()};
		source->texture = gs_texture_create(image.width(), image.height(), GS_RGBA, 1, planes, GS_DYNAMIC);
		source->texture_revision = revision;
	}
	if (source->texture != nullptr)
		obs_source_draw(source->texture, 0, 0, 0, 0, false);
}

obs_source_info overlay_source_info()
{
	obs_source_info info{};
	info.id = kPresentationOverlaySourceId;
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

obs_source_t *scene_source(const std::string &name)
{
	auto *source = obs_get_source_by_name(name.c_str());
	if (source != nullptr && obs_source_get_type(source) != OBS_SOURCE_TYPE_SCENE) {
		obs_source_release(source);
		return nullptr;
	}
	return source;
}

obs_sceneitem_t *overlay_item(obs_scene_t *scene, const std::string &name)
{
	auto *item = obs_scene_find_source(scene, name.c_str());
	if (item == nullptr)
		return nullptr;
	auto *source = obs_sceneitem_get_source(item);
	return source != nullptr && std::string(obs_source_get_id(source)) == kPresentationOverlaySourceId ? item
													   : nullptr;
}

bool resolve_binding(const std::string &scene_name_value, const std::string &source_name_value,
		     obs_source_t **scene_source_value, obs_sceneitem_t **item_value, obs_source_t **overlay)
{
	*scene_source_value = scene_source(scene_name_value);
	if (*scene_source_value == nullptr)
		return false;
	auto *scene = obs_scene_from_source(*scene_source_value);
	*item_value = scene != nullptr ? overlay_item(scene, source_name_value) : nullptr;
	if (*item_value == nullptr) {
		obs_source_release(*scene_source_value);
		*scene_source_value = nullptr;
		return false;
	}
	*overlay = obs_sceneitem_get_source(*item_value);
	return *overlay != nullptr;
}

void position_item(obs_sceneitem_t *item, const std::string &anchor, const obs_video_info &video, float margin)
{
	auto *source = obs_sceneitem_get_source(item);
	const float width = static_cast<float>(obs_source_get_width(source));
	const float height = static_cast<float>(obs_source_get_height(source));
	float x = margin;
	float y = margin;
	if (anchor == "top_center" || anchor == "bottom_center")
		x = std::max(0.0f, (static_cast<float>(video.base_width) - width) / 2.0f);
	else if (anchor == "top_right" || anchor == "center_right" || anchor == "bottom_right")
		x = std::max(0.0f, static_cast<float>(video.base_width) - width - margin);
	if (anchor == "center_left" || anchor == "center_right")
		y = std::max(0.0f, (static_cast<float>(video.base_height) - height) / 2.0f);
	else if (anchor == "bottom_left" || anchor == "bottom_center" || anchor == "bottom_right")
		y = std::max(0.0f, static_cast<float>(video.base_height) - height - margin);
	vec2 position{x, y};
	vec2 scale{1.0f, 1.0f};
	obs_sceneitem_set_alignment(item, OBS_ALIGN_LEFT | OBS_ALIGN_TOP);
	obs_sceneitem_set_pos(item, &position);
	obs_sceneitem_set_scale(item, &scale);
	obs_sceneitem_set_order(item, OBS_ORDER_MOVE_TOP);
}

std::string anchor_for_item(obs_sceneitem_t *item, float margin)
{
	obs_video_info video{};
	if (!obs_get_video_info(&video))
		return "custom";
	auto *source = obs_sceneitem_get_source(item);
	const float width = static_cast<float>(obs_source_get_width(source));
	const float height = static_cast<float>(obs_source_get_height(source));
	vec2 position{};
	obs_sceneitem_get_pos(item, &position);
	const float xs[] = {margin, std::max(0.0f, (video.base_width - width) / 2.0f),
			    std::max(0.0f, video.base_width - width - margin)};
	const float ys[] = {margin, std::max(0.0f, (video.base_height - height) / 2.0f),
			    std::max(0.0f, video.base_height - height - margin)};
	const char *anchors[3][3] = {{"top_left", "top_center", "top_right"},
				     {"center_left", "custom", "center_right"},
				     {"bottom_left", "bottom_center", "bottom_right"}};
	for (int y = 0; y < 3; ++y)
		for (int x = 0; x < 3; ++x)
			if (std::fabs(position.x - xs[x]) <= 1.0f && std::fabs(position.y - ys[y]) <= 1.0f)
				return anchors[y][x];
	return "custom";
}

} // namespace

bool validate_presentation_overlay_content(const PresentationOverlayContent &content)
{
	if (!valid_overlay_text(QString::fromStdString(content.title), 128))
		return false;
	QJsonParseError error{};
	const auto document = QJsonDocument::fromJson(QByteArray::fromStdString(content.rows_json), &error);
	if (error.error != QJsonParseError::NoError || !document.isArray() || document.array().size() > 8)
		return false;
	for (const auto entry : document.array()) {
		if (!entry.isObject())
			return false;
		const auto row = entry.toObject();
		if (row.size() != 2 || !row.contains(QStringLiteral("label")) ||
		    !row.contains(QStringLiteral("value")) || !row.value(QStringLiteral("label")).isString() ||
		    !row.value(QStringLiteral("value")).isString() ||
		    !valid_overlay_text(row.value(QStringLiteral("label")).toString(), 32) ||
		    !valid_overlay_text(row.value(QStringLiteral("value")).toString(), 64))
			return false;
	}
	if (!content.logo_path.empty()) {
		const QFileInfo file(QString::fromStdString(content.logo_path));
		const QString suffix = file.suffix().toLower();
		if (!file.isAbsolute() || !file.isFile() || file.size() > kMaximumLogoBytes ||
		    (suffix != QStringLiteral("png") && suffix != QStringLiteral("jpg") &&
		     suffix != QStringLiteral("jpeg") && suffix != QStringLiteral("webp")) ||
		    QImage(file.absoluteFilePath()).isNull())
			return false;
	}
	return true;
}

void register_presentation_overlay_source()
{
	static obs_source_info info = overlay_source_info();
	obs_register_source(&info);
}

obs_data_t *create_presentation_overlay(const std::string &scene_name_value, const std::string &source_name_value,
					const PresentationOverlayLayout &layout)
{
	auto *result = obs_data_create();
	auto *scene_source_value = scene_source(scene_name_value);
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
	auto *scene = obs_scene_from_source(scene_source_value);
	auto *item = scene != nullptr ? overlay_item(scene, source_name_value) : nullptr;
	obs_source_t *overlay = nullptr;
	if (item == nullptr) {
		overlay = obs_get_source_by_name(source_name_value.c_str());
		if (overlay != nullptr && std::string(obs_source_get_id(overlay)) != kPresentationOverlaySourceId) {
			set_error(result, "OBS_TARGET_AMBIGUOUS");
			obs_source_release(overlay);
			obs_source_release(scene_source_value);
			return result;
		}
		if (overlay == nullptr) {
			auto *settings = obs_data_create();
			obs_data_set_string(settings, "anchor", layout.anchor.c_str());
			obs_data_set_int(settings, "opacity", layout.opacity);
			obs_data_set_int(settings, "margin", layout.margin);
			obs_data_set_int(settings, "width", layout.width);
			overlay = obs_source_create(kPresentationOverlaySourceId, source_name_value.c_str(), settings,
						    nullptr);
			obs_data_release(settings);
		}
		if (overlay != nullptr)
			item = obs_scene_add(scene, overlay);
	}
	if (item == nullptr)
		set_error(result, "OBS_REQUEST_FAILED");
	else {
		auto *settings = obs_source_get_settings(obs_sceneitem_get_source(item));
		obs_data_set_string(settings, "anchor", layout.anchor.c_str());
		obs_data_set_int(settings, "opacity", layout.opacity);
		obs_data_set_int(settings, "margin", layout.margin);
		obs_data_set_int(settings, "width", layout.width);
		obs_source_update(obs_sceneitem_get_source(item), settings);
		obs_data_release(settings);
		position_item(item, layout.anchor, video, static_cast<float>(layout.margin));
		obs_data_set_bool(result, "accepted", true);
	}
	if (overlay != nullptr)
		obs_source_release(overlay);
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *get_presentation_overlay(const std::string &scene_name_value, const std::string &source_name_value)
{
	auto *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name_value, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	auto *settings = obs_source_get_settings(overlay);
	const int margin = static_cast<int>(obs_data_get_int(settings, "margin"));
	obs_data_set_string(result, "sceneName", scene_name_value.c_str());
	obs_data_set_int(result, "sceneItemId", obs_sceneitem_get_id(item));
	obs_data_set_string(result, "sourceName", source_name_value.c_str());
	obs_data_set_string(result, "sourceKind", kPresentationOverlaySourceId);
	obs_data_set_string(result, "theme", "dcc_mcp_dark");
	obs_data_set_string(result, "anchor", anchor_for_item(item, static_cast<float>(margin)).c_str());
	obs_data_set_int(result, "opacity", obs_data_get_int(settings, "opacity"));
	obs_data_set_int(result, "margin", margin);
	obs_data_set_int(result, "width", obs_data_get_int(settings, "width"));
	obs_data_set_bool(result, "visible", obs_data_get_bool(settings, "visible"));
	obs_data_set_string(result, "title", obs_data_get_string(settings, "title"));
	obs_data_set_string(result, "rowsJson", obs_data_get_string(settings, "rowsJson"));
	obs_data_set_string(result, "logoPath", obs_data_get_string(settings, "logoPath"));
	obs_data_release(settings);
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *set_presentation_overlay_layout(const std::string &scene_name_value, const std::string &source_name_value,
					    const PresentationOverlayLayout &layout)
{
	auto *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name_value, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	obs_video_info video{};
	if (!obs_get_video_info(&video) || video.base_width == 0 || video.base_height == 0) {
		set_error(result, "OBS_INSTANCE_NOT_READY");
		obs_source_release(scene_source_value);
		return result;
	}
	auto *settings = obs_source_get_settings(overlay);
	obs_data_set_string(settings, "anchor", layout.anchor.c_str());
	obs_data_set_int(settings, "opacity", layout.opacity);
	obs_data_set_int(settings, "margin", layout.margin);
	obs_data_set_int(settings, "width", layout.width);
	obs_source_update(overlay, settings);
	obs_data_release(settings);
	position_item(item, layout.anchor, video, static_cast<float>(layout.margin));
	obs_data_set_bool(result, "accepted", true);
	obs_source_release(scene_source_value);
	return result;
}

obs_data_t *update_presentation_overlay(const std::string &scene_name_value, const std::string &source_name_value,
					const PresentationOverlayContent &content)
{
	auto *result = obs_data_create();
	obs_source_t *scene_source_value = nullptr;
	obs_sceneitem_t *item = nullptr;
	obs_source_t *overlay = nullptr;
	if (!resolve_binding(scene_name_value, source_name_value, &scene_source_value, &item, &overlay)) {
		set_error(result, "OBS_SOURCE_NOT_FOUND");
		return result;
	}
	auto *settings = obs_source_get_settings(overlay);
	obs_data_set_string(settings, "title", content.title.c_str());
	obs_data_set_string(settings, "rowsJson", content.rows_json.c_str());
	obs_data_set_string(settings, "logoPath", content.logo_path.c_str());
	obs_data_set_bool(settings, "visible", content.visible);
	obs_source_update(overlay, settings);
	const std::string anchor = obs_data_get_string(settings, "anchor");
	const int margin = static_cast<int>(obs_data_get_int(settings, "margin"));
	obs_data_release(settings);
	obs_video_info video{};
	if (obs_get_video_info(&video))
		position_item(item, anchor, video, static_cast<float>(margin));
	obs_data_set_bool(result, "accepted", true);
	obs_source_release(scene_source_value);
	return result;
}

} // namespace dcc_mcp_obs
