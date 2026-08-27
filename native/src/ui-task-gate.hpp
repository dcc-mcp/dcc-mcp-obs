#pragma once

#include <atomic>
#include <cstdint>

namespace dcc_mcp_obs {

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

private:
	enum class State : uint8_t { Pending, Started, Cancelled };

	std::atomic<State> state_{State::Pending};
};

} // namespace dcc_mcp_obs
