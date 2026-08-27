#include "../src/ui-task-gate.hpp"

#include <atomic>
#include <cassert>
#include <chrono>
#include <thread>

int main()
{
	dcc_mcp_obs::UiTaskGate delayed;
	std::atomic<bool> mutated{false};
	std::thread worker([&] {
		std::this_thread::sleep_for(std::chrono::milliseconds(40));
		assert(!delayed.try_start());
		assert(!delayed.claim_mutation());
	});
	assert(delayed.cancel_pending());
	worker.join();
	assert(!mutated.load());

	dcc_mcp_obs::UiTaskGate started;
	assert(started.claim_mutation());
	mutated = true;
	assert(!started.cancel_pending());
	assert(mutated.load());

	dcc_mcp_obs::UiTaskGate late_cancel;
	std::atomic<bool> probe_entered{false};
	std::atomic<bool> release_probe{false};
	mutated = false;
	std::thread probing_worker([&] {
		probe_entered = true;
		while (!release_probe.load())
			std::this_thread::yield();
		if (late_cancel.claim_mutation())
			mutated = true;
	});
	while (!probe_entered.load())
		std::this_thread::yield();
	assert(late_cancel.cancel_pending());
	release_probe = true;
	probing_worker.join();
	assert(!mutated.load());
}
