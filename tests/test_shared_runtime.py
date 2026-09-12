import os
import sys
import types
from dataclasses import dataclass

import pytest

from dcc_mcp_obs import shared_runtime
from dcc_mcp_obs.__version__ import __version__


def test_shared_runtime_requires_runtime_or_reports_missing(monkeypatch):
    monkeypatch.setitem(os.environ, "DCC_MCP_PYTHON_EXECUTABLE", "")
    try:
        shared_runtime.require_runtime()
    except RuntimeError as exc:
        assert str(exc).startswith("DCC_MCP_RUNTIME_MISSING:")
    else:
        assert os.environ["DCC_MCP_PYTHON_EXECUTABLE"]


@dataclass
class _Handshake:
    adapter_id: str = "obs"
    adapter_version: str = __version__
    runtime_id: str = "dcc-mcp-external"
    runtime_version: str = "0.1.0"
    capabilities_fingerprint: str = "a" * 64


def _install_fake_runtime(monkeypatch, handshake: _Handshake) -> None:
    bootstrap = types.ModuleType("dcc_mcp_runtime.bootstrap")
    bootstrap.require_adapter = lambda adapter_id: types.SimpleNamespace(handshake=handshake)
    package = types.ModuleType("dcc_mcp_runtime")
    package.bootstrap = bootstrap
    monkeypatch.setitem(sys.modules, "dcc_mcp_runtime", package)
    monkeypatch.setitem(sys.modules, "dcc_mcp_runtime.bootstrap", bootstrap)


def test_shared_runtime_exports_verified_handshake_metadata(monkeypatch):
    _install_fake_runtime(monkeypatch, _Handshake())
    shared_runtime.require_runtime()
    assert os.environ["DCC_MCP_RUNTIME_ID"] == "dcc-mcp-external"
    assert os.environ["DCC_MCP_RUNTIME_VERSION"] == "0.1.0"
    assert os.environ["DCC_MCP_RUNTIME_CAPABILITIES_FINGERPRINT"] == "a" * 64


def test_shared_runtime_rejects_stale_adapter_manifest(monkeypatch):
    _install_fake_runtime(monkeypatch, _Handshake(adapter_version="0.0.1"))
    with pytest.raises(RuntimeError, match="ADAPTER_VERSION_MISMATCH"):
        shared_runtime.require_runtime()
