"""Shared pytest fixtures for the MCP server test suite.

Two key responsibilities:

  1. Make the in-repo package importable regardless of where pytest is
     invoked from (mirrors the convention used in tests/unit/).

  2. Provide a ``mem0_client_stub`` fixture — a fake ``Mem0Client`` with
     configurable behaviour — so tool tests don't need a running Mem0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def mem0_client_stub():
    """A fake Mem0Client whose methods are AsyncMocks with sensible defaults.

    Tests can override ``mem0_client_stub.search.return_value = [...]`` etc.
    The ``aclose`` method is wired up so the lifespan teardown doesn't
    complain.
    """
    stub = SimpleNamespace(
        search=AsyncMock(return_value=[]),
        add=AsyncMock(return_value=[{"id": "abc", "memory": "stored"}]),
        health=AsyncMock(return_value=True),
        aclose=AsyncMock(return_value=None),
    )
    return stub


@pytest.fixture
def install_stub_mem0(mem0_client_stub, monkeypatch):
    """Install the stub as the module-level ``_mem0`` for tool tests.

    The MCP server reads ``_mem0`` at call time, so swapping it here is
    enough to control what the tools see without touching the lifespan.
    """
    import mcp_server.server as server_mod

    monkeypatch.setattr(server_mod, "_mem0", mem0_client_stub)
    return mem0_client_stub
