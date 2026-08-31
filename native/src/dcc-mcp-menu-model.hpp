#pragma once

#include <array>
#include <functional>
#include <string>
#include <string_view>

namespace dcc_mcp_obs {

inline constexpr std::string_view kDccMcpMenuTitle = "DCC MCP";
inline constexpr std::string_view kDccMcpMenuObjectName = "dccMcpObsMenu";

enum class DccMcpMenuAction {
	ServerStatus,
	AddAgentInputOverlay,
	OpenGatewayAdmin,
	About,
};

struct DccMcpMenuEntry {
	DccMcpMenuAction action;
	std::string_view id;
	std::string_view label;
	bool separator_before;
};

inline constexpr std::array<DccMcpMenuEntry, 4> kDccMcpMenuEntries = {{
	{DccMcpMenuAction::ServerStatus, "serverStatus", "Server Status...", false},
	{DccMcpMenuAction::AddAgentInputOverlay, "addAgentInputOverlay", "Add Agent Input Overlay", false},
	{DccMcpMenuAction::OpenGatewayAdmin, "openGatewayAdmin", "Open Gateway Admin", true},
	{DccMcpMenuAction::About, "about", "About DCC-MCP OBS", true},
}};

struct DccMcpMenuCallbacks {
	std::function<void()> server_status;
	std::function<void()> add_agent_input_overlay;
	std::function<void()> open_gateway_admin;
	std::function<void()> about;
};

inline bool dispatch_dcc_mcp_menu_action(DccMcpMenuAction action, const DccMcpMenuCallbacks &callbacks)
{
	const std::function<void()> *callback = nullptr;
	switch (action) {
	case DccMcpMenuAction::ServerStatus:
		callback = &callbacks.server_status;
		break;
	case DccMcpMenuAction::AddAgentInputOverlay:
		callback = &callbacks.add_agent_input_overlay;
		break;
	case DccMcpMenuAction::OpenGatewayAdmin:
		callback = &callbacks.open_gateway_admin;
		break;
	case DccMcpMenuAction::About:
		callback = &callbacks.about;
		break;
	}
	if (callback == nullptr || !*callback)
		return false;
	(*callback)();
	return true;
}

struct DccMcpMenuStatus {
	std::string plugin_version;
	std::string obs_version;
	std::string scene_name;
	bool native_bridge_ready = false;
	bool recording_active = false;
	bool streaming_active = false;
	bool replay_buffer_active = false;
	bool virtual_camera_active = false;
};

inline std::string format_dcc_mcp_menu_status(const DccMcpMenuStatus &status)
{
	const auto activity = [](bool active) {
		return active ? "Active" : "Inactive";
	};
	std::string rendered = "DCC-MCP OBS " + status.plugin_version;
	rendered += "\nNative bridge: ";
	rendered += status.native_bridge_ready ? "Ready" : "Unavailable";
	rendered += "\nOBS: " + status.obs_version;
	rendered += "\nScene: " + (status.scene_name.empty() ? std::string("<none>") : status.scene_name);
	rendered += "\nRecording: ";
	rendered += activity(status.recording_active);
	rendered += "\nStreaming: ";
	rendered += activity(status.streaming_active);
	rendered += "\nReplay buffer: ";
	rendered += activity(status.replay_buffer_active);
	rendered += "\nVirtual camera: ";
	rendered += activity(status.virtual_camera_active);
	return rendered;
}

} // namespace dcc_mcp_obs
