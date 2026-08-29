#include "../src/ui-task-gate.hpp"

#include <chrono>

#define CHECK(condition) \
	do { \
		if (!(condition)) \
			return 1; \
	} while (false)

int main()
{
	dcc_mcp_obs::UiTaskGate delayed;
	CHECK(delayed.cancel_pending());
	CHECK(!delayed.try_start());
	CHECK(!delayed.claim_mutation());

	dcc_mcp_obs::UiTaskGate expired;
	CHECK(!expired.claim_mutation(std::chrono::steady_clock::now() - std::chrono::seconds(1)));
	const auto fake_system_now = std::chrono::system_clock::time_point(std::chrono::milliseconds(1000));
	const auto fake_steady_now = std::chrono::steady_clock::time_point(std::chrono::milliseconds(500));
	const auto converted = dcc_mcp_obs::steady_deadline_from_epoch_ms(2500, fake_steady_now, fake_system_now);
	const auto converted_delta = converted - fake_steady_now;
	CHECK(converted_delta >= std::chrono::milliseconds(1000));
	CHECK(converted_delta <= std::chrono::milliseconds(2000));
	CHECK(dcc_mcp_obs::steady_deadline_from_epoch_ms(999, fake_steady_now, fake_system_now) == fake_steady_now);

	dcc_mcp_obs::UiTaskGate started;
	CHECK(started.claim_mutation());
	CHECK(!started.cancel_pending());
	return 0;
}
