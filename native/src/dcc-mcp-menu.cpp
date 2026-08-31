#include "dcc-mcp-menu.hpp"

#include "dcc-mcp-menu-model.hpp"

#include <QMainWindow>
#include <QMenu>
#include <QMenuBar>
#include <QAction>
#include <QString>

#include <utility>

namespace dcc_mcp_obs {

DccMcpMenu::DccMcpMenu(QMainWindow *main_window, DccMcpMenuCallbacks callbacks)
	: main_window_(main_window),
	  callbacks_(std::move(callbacks))
{
}

DccMcpMenu::~DccMcpMenu()
{
	remove();
}

bool DccMcpMenu::install()
{
	if (main_window_ == nullptr)
		return false;
	auto *menu_bar = main_window_->menuBar();
	if (menu_bar == nullptr)
		return false;
	if (menu_ != nullptr)
		return true;

	const QString object_name =
		QString::fromUtf8(kDccMcpMenuObjectName.data(), static_cast<qsizetype>(kDccMcpMenuObjectName.size()));
	if (auto *existing = menu_bar->findChild<QMenu *>(object_name, Qt::FindDirectChildrenOnly)) {
		menu_bar->removeAction(existing->menuAction());
		delete existing;
	}

	menu_ = new QMenu(QString::fromUtf8(kDccMcpMenuTitle.data(), static_cast<qsizetype>(kDccMcpMenuTitle.size())),
			  menu_bar);
	menu_->setObjectName(object_name);
	menu_bar->addMenu(menu_);
	for (const auto &entry : kDccMcpMenuEntries) {
		if (entry.separator_before)
			menu_->addSeparator();
		auto *action = menu_->addAction(
			QString::fromUtf8(entry.label.data(), static_cast<qsizetype>(entry.label.size())));
		action->setObjectName(
			QStringLiteral("dccMcpObsMenu.%1")
				.arg(QString::fromUtf8(entry.id.data(), static_cast<qsizetype>(entry.id.size()))));
		QObject::connect(action, &QAction::triggered, menu_, [this, menu_action = entry.action]() {
			dispatch_dcc_mcp_menu_action(menu_action, callbacks_);
		});
	}
	return true;
}

void DccMcpMenu::remove()
{
	if (menu_ == nullptr)
		return;
	if (main_window_ != nullptr && main_window_->menuBar() != nullptr)
		main_window_->menuBar()->removeAction(menu_->menuAction());
	delete menu_.data();
	menu_ = nullptr;
}

} // namespace dcc_mcp_obs
