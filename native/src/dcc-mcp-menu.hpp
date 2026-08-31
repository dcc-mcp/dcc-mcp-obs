#pragma once

#include "dcc-mcp-menu-model.hpp"

#include <QPointer>

class QMainWindow;
class QMenu;

namespace dcc_mcp_obs {

class DccMcpMenu final {
public:
	DccMcpMenu(QMainWindow *main_window, DccMcpMenuCallbacks callbacks);
	~DccMcpMenu();

	DccMcpMenu(const DccMcpMenu &) = delete;
	DccMcpMenu &operator=(const DccMcpMenu &) = delete;

	bool install();
	void remove();

private:
	QPointer<QMainWindow> main_window_;
	QPointer<QMenu> menu_;
	DccMcpMenuCallbacks callbacks_;
};

} // namespace dcc_mcp_obs
