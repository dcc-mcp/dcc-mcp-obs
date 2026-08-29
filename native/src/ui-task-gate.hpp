#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>

namespace dcc_mcp_obs {

inline std::chrono::steady_clock::time_point
steady_deadline_from_epoch_ms(uint64_t deadline_at_ms, std::chrono::steady_clock::time_point steady_now,
			      std::chrono::system_clock::time_point system_now)
{
	const auto system_deadline = std::chrono::system_clock::time_point(std::chrono::milliseconds(deadline_at_ms));
	const auto remaining = system_deadline - system_now;
	if (remaining <= std::chrono::system_clock::duration::zero())
		return steady_now;
	return steady_now + std::chrono::duration_cast<std::chrono::steady_clock::duration>(remaining);
}

class UiTaskGate {
public:
	bool try_start()
	{
		State expected = State::Pending;
		return state_.compare_exchange_strong(expected, State::Started);
	}

	bool cancel_pending()
	{
		State expected = State::Pending;
		return state_.compare_exchange_strong(expected, State::Cancelled);
	}

	bool claim_mutation()
	{
		State expected = State::Pending;
		return state_.compare_exchange_strong(expected, State::Started);
	}

	bool claim_mutation(std::chrono::steady_clock::time_point deadline)
	{
		State expected = State::Pending;
		if (!state_.compare_exchange_strong(expected, State::Started))
			return false;
		if (std::chrono::steady_clock::now() >= deadline) {
			state_.store(State::Cancelled);
			return false;
		}
		return true;
	}

private:
	enum class State : uint8_t { Pending, Started, Cancelled };

	std::atomic<State> state_{State::Pending};
};

} // namespace dcc_mcp_obs
