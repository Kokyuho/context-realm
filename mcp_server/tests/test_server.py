"""Unit tests for mcp_server/server.py.

Three areas:

  * ``TestTokenAuthMiddleware`` — token enforcement on /mcp and /sse,
    exemption for /health and other paths, behaviour when the token
    is empty (local dev).

  * ``TestToolSearch`` / ``TestToolAdd`` — the two MCP tools called
    directly with a stubbed ``_mem0``.

  * ``TestFormatting`` — the private ``_format_memory`` helper
    tolerates the various Mem0 response shapes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import mcp_server.server as server_mod
from mcp_server.server import _format_memory

# ─── Auth middleware ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTokenAuthMiddleware:
    """Token enforcement on the MCP endpoints.

    We exercise the middleware directly by constructing it with a mock
    ``call_next`` and a Starlette ``Request`` whose ``headers`` /
    ``url.path`` we control. This is faster and clearer than spinning up
    the whole ASGI app for what is essentially a header-check.
    """

    async def _run_middleware(self, request, call_next):
        # The middleware class is private but stable; we reach for it by
        # name so tests don't depend on the import surface of server.py.
        mw = server_mod._TokenAuthMiddleware(app=MagicMock())
        return await mw.dispatch(request, call_next)

    def _request(self, path: str, authorization: str | None = None) -> MagicMock:
        request = MagicMock()
        request.url.path = path
        headers: dict = {}
        if authorization is not None:
            headers["authorization"] = authorization
        request.headers = headers
        return request

    async def test_rejects_request_to_mcp_without_token(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        call_next = AsyncMock(return_value=MagicMock(body=b"ok"))
        request = self._request("/mcp/", authorization=None)
        response = await self._run_middleware(request, call_next)
        # 401, not the upstream response.
        assert response.status_code == 401
        call_next.assert_not_called()

    async def test_rejects_request_with_wrong_token(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        call_next = AsyncMock(return_value=MagicMock(body=b"ok"))
        request = self._request("/mcp/", authorization="Token wrong-value")
        response = await self._run_middleware(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    async def test_accepts_request_with_correct_token(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        upstream = MagicMock(body=b"upstream")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/mcp/", authorization="Token secret-abc")
        response = await self._run_middleware(request, call_next)
        # Middleware passed through; upstream response is what we got.
        assert response is upstream
        call_next.assert_called_once()

    async def test_accepts_bearer_token_for_compatibility(self, monkeypatch) -> None:
        # Some MCP clients default to Bearer; we accept it as well.
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        upstream = MagicMock(body=b"upstream")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/sse/", authorization="Bearer secret-abc")
        response = await self._run_middleware(request, call_next)
        assert response is upstream

    async def test_health_is_exempt_from_auth(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        upstream = MagicMock(body=b"ok")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/health", authorization=None)
        response = await self._run_middleware(request, call_next)
        assert response is upstream

    async def test_root_is_exempt_from_auth(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        upstream = MagicMock(body=b"ok")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/", authorization=None)
        response = await self._run_middleware(request, call_next)
        assert response is upstream

    async def test_empty_token_means_auth_disabled(self, monkeypatch) -> None:
        # When MCP_TOKEN is blank, the middleware is a no-op. This matches
        # MEM0_AUTH_DISABLED=true on the Mem0 side.
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "")
        upstream = MagicMock(body=b"ok")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/mcp/", authorization=None)
        response = await self._run_middleware(request, call_next)
        assert response is upstream

    async def test_empty_token_with_header_still_passes(self, monkeypatch) -> None:
        # Same as above — we don't validate when there's no expected token.
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "")
        upstream = MagicMock(body=b"ok")
        call_next = AsyncMock(return_value=upstream)
        request = self._request("/mcp/", authorization="Token anything")
        response = await self._run_middleware(request, call_next)
        assert response is upstream

    async def test_token_comparison_is_exact(self, monkeypatch) -> None:
        # Substring/whitespace matches must NOT succeed — a security check
        # that returns 200 on a near-miss is worse than one that 401s.
        monkeypatch.setattr(server_mod, "MCP_TOKEN", "secret-abc")
        call_next = AsyncMock(return_value=MagicMock(body=b"ok"))
        for bad in ("Token secret", "Token secret-ab", "Token secret-abc ", "Token SECRET-ABC"):
            request = self._request("/mcp/", authorization=bad)
            response = await self._run_middleware(request, call_next)
            assert response.status_code == 401, f"token {bad!r} should have been rejected"


# ─── Tool: search_memories ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestToolSearch:
    async def test_returns_text_for_each_memory(self, install_stub_mem0, mem0_client_stub) -> None:
        mem0_client_stub.search.return_value = [
            {"memory": "User likes cats"},
            {"memory": "User lives in Berlin"},
        ]
        result = await server_mod.search_memories(query="pets", limit=5)
        assert result == ["User likes cats", "User lives in Berlin"]
        mem0_client_stub.search.assert_called_once_with(query="pets", limit=5)

    async def test_returns_empty_list_when_mem0_returns_empty(
        self, install_stub_mem0, mem0_client_stub
    ) -> None:
        mem0_client_stub.search.return_value = []
        assert await server_mod.search_memories(query="x") == []

    async def test_returns_empty_list_when_mem0_raises(
        self, install_stub_mem0, mem0_client_stub
    ) -> None:
        # The tool must not surface Mem0 errors as exceptions — search is
        # a recall query, the calling agent should get an empty list and
        # carry on.
        from mcp_server.mem0_client import Mem0Error

        mem0_client_stub.search.side_effect = Mem0Error("down")
        assert await server_mod.search_memories(query="x") == []

    async def test_returns_empty_list_when_mem0_client_uninitialised(self, monkeypatch) -> None:
        # Lifespan not run — the search tool should not blow up, just
        # return nothing.
        monkeypatch.setattr(server_mod, "_mem0", None)
        assert await server_mod.search_memories(query="x") == []


# ─── Tool: add_memory ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestToolAdd:
    async def test_returns_count_of_stored_memories(
        self, install_stub_mem0, mem0_client_stub
    ) -> None:
        mem0_client_stub.add.return_value = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        count = await server_mod.add_memory(text="User prefers dark mode")
        assert count == 3
        mem0_client_stub.add.assert_called_once_with(text="User prefers dark mode")

    async def test_returns_zero_when_mem0_stored_nothing(
        self, install_stub_mem0, mem0_client_stub
    ) -> None:
        # The extraction LLM may decide the input is not worth storing.
        mem0_client_stub.add.return_value = []
        assert await server_mod.add_memory(text="hi") == 0

    async def test_raises_runtime_error_when_mem0_unreachable(
        self, install_stub_mem0, mem0_client_stub
    ) -> None:
        # Writes are different from reads: a silent failure would mean the
        # agent thinks it stored something it didn't. We raise so the
        # client gets feedback.
        from mcp_server.mem0_client import Mem0Error

        mem0_client_stub.add.side_effect = Mem0Error("503 service unavailable")
        with pytest.raises(RuntimeError, match="503"):
            await server_mod.add_memory(text="x")

    async def test_raises_runtime_error_when_mem0_client_uninitialised(self, monkeypatch) -> None:
        monkeypatch.setattr(server_mod, "_mem0", None)
        with pytest.raises(RuntimeError, match="not ready"):
            await server_mod.add_memory(text="x")


# ─── Memory formatting ─────────────────────────────────────────────────────


class TestFormatting:
    def test_prefers_memory_key(self) -> None:
        assert _format_memory({"memory": "hello", "text": "ignored"}) == "hello"

    def test_falls_back_to_text_then_content(self) -> None:
        assert _format_memory({"text": "from text"}) == "from text"
        assert _format_memory({"content": "from content"}) == "from content"

    def test_strips_whitespace(self) -> None:
        assert _format_memory({"memory": "  hello  "}) == "hello"

    def test_falls_back_to_str_when_no_recognised_key(self) -> None:
        result = _format_memory({"weird": "shape"})
        assert "weird" in result and "shape" in result

    def test_skips_empty_strings(self) -> None:
        # The caller (``search_memories``) checks ``if m`` so we just
        # confirm the helper returns something stable for empty input.
        result = _format_memory({"memory": ""})
        assert isinstance(result, str)
