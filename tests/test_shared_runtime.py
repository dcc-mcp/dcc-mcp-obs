import os

from dcc_mcp_obs import shared_runtime


def test_shared_runtime_requires_runtime_or_reports_missing(monkeypatch):
    monkeypatch.setitem(os.environ, "DCC_MCP_PYTHON_EXECUTABLE", "")
    try:
        shared_runtime.require_runtime()
    except RuntimeError as exc:
        assert str(exc).startswith("DCC_MCP_RUNTIME_MISSING:")
    else:
        assert os.environ["DCC_MCP_PYTHON_EXECUTABLE"]
