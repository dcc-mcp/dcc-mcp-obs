#include "../src/dcc-mcp-menu-model.hpp"

#include <set>
#include <string>

#define CHECK(condition) \
	do { \
		if (!(condition)) \
			return 1; \
	} while (false)

int main()
{
	using dcc_mcp_obs::DccMcpMenuAction;
	using dcc_mcp_obs::DccMcpMenuCallbacks;
	using dcc_mcp_obs::DccMcpMenuStatus;

	CHECK(dcc_mcp_obs::kDccMcpMenuTitle == std::string_view("DCC MCP"));
	CHECK(dcc_mcp_obs::kDccMcpMenuObjectName == std::string_view("dccMcpObsMenu"));
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries.size() == 4);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[0].action == DccMcpMenuAction::ServerStatus);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[0].label == std::string_view("Server Status..."));
	CHECK(!dcc_mcp_obs::kDccMcpMenuEntries[0].separator_before);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[1].action == DccMcpMenuAction::AddAgentInputOverlay);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[1].label == std::string_view("Add Agent Input Overlay"));
	CHECK(!dcc_mcp_obs::kDccMcpMenuEntries[1].separator_before);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[2].action == DccMcpMenuAction::OpenGatewayAdmin);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[2].label == std::string_view("Open Gateway Admin"));
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[2].separator_before);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[3].action == DccMcpMenuAction::About);
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[3].label == std::string_view("About DCC-MCP OBS"));
	CHECK(dcc_mcp_obs::kDccMcpMenuEntries[3].separator_before);

	std::set<std::string_view> ids;
	for (const auto &entry : dcc_mcp_obs::kDccMcpMenuEntries)
		CHECK(ids.insert(entry.id).second);

	DccMcpMenuStatus status;
	status.plugin_version = "1.1.0";
	status.obs_version = "32.2.1";
	status.scene_name = "RL - The Bazaar";
	status.native_bridge_ready = true;
	status.recording_active = false;
	status.streaming_active = false;
	status.replay_buffer_active = true;
	status.virtual_camera_active = false;
	const std::string rendered = dcc_mcp_obs::format_dcc_mcp_menu_status(status);
	CHECK(rendered.find("DCC-MCP OBS 1.1.0") != std::string::npos);
	CHECK(rendered.find("Native bridge: Ready") != std::string::npos);
	CHECK(rendered.find("OBS: 32.2.1") != std::string::npos);
	CHECK(rendered.find("Scene: RL - The Bazaar") != std::string::npos);
	CHECK(rendered.find("Recording: Inactive") != std::string::npos);
	CHECK(rendered.find("Streaming: Inactive") != std::string::npos);
	CHECK(rendered.find("Replay buffer: Active") != std::string::npos);
	CHECK(rendered.find("Virtual camera: Inactive") != std::string::npos);

	int status_calls = 0;
	int overlay_calls = 0;
	int admin_calls = 0;
	int about_calls = 0;
	DccMcpMenuCallbacks callbacks{
		[&status_calls]() { ++status_calls; },
		[&overlay_calls]() { ++overlay_calls; },
		[&admin_calls]() { ++admin_calls; },
		[&about_calls]() { ++about_calls; },
	};
	CHECK(dcc_mcp_obs::dispatch_dcc_mcp_menu_action(DccMcpMenuAction::ServerStatus, callbacks));
	CHECK(dcc_mcp_obs::dispatch_dcc_mcp_menu_action(DccMcpMenuAction::AddAgentInputOverlay, callbacks));
	CHECK(dcc_mcp_obs::dispatch_dcc_mcp_menu_action(DccMcpMenuAction::OpenGatewayAdmin, callbacks));
	CHECK(dcc_mcp_obs::dispatch_dcc_mcp_menu_action(DccMcpMenuAction::About, callbacks));
	CHECK(status_calls == 1);
	CHECK(overlay_calls == 1);
	CHECK(admin_calls == 1);
	CHECK(about_calls == 1);
	return 0;
}
