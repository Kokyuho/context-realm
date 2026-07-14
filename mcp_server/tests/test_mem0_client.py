"""Unit tests for mcp_server/mem0_client.py.

The Mem0 client is a thin async wrapper. Tests focus on the things
that actually go wrong in production:

  * Wrong response shape (v0.x vs v2.x)
  * Missing/invalid token → 401/403
  * Connection errors translate to ``Mem0Error``
  * Trailing-slash tolerance on the base URL
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_server.mem0_client import Mem0Client, Mem0Error


def _mock_response(status_code: int, *, json_data=None, text: str = ""):
    """Build a fake httpx.Response with the given status and body."""
    return SimpleNamespace(
        status_code=status_code,
        content=b"" if json_data is None else b"{}",
        text=text,
        json=lambda: json_data,
    )


@pytest.mark.asyncio
class TestSearch:
    async def test_returns_list_when_mem0_returns_list(self) -> None:
        client = Mem0Client(base_url="http://m:8000", user_id="alice")
        response = _mock_response(200, json_data=[{"memory": "fact"}])
        # httpx.AsyncClient.post returns a Response; we stub it via monkeypatch.
        client._client = httpx.AsyncClient(base_url="http://m:8000")  # real client, patched below

        async def fake_post(url, json=None, **kwargs):
            assert url.endswith("/v1/memories/search/")
            assert json["query"] == "q"
            assert json["user_id"] == "alice"
            return response

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            results = await client.search("q")
        finally:
            await client.aclose()
        assert results == [{"memory": "fact"}]

    async def test_unwraps_results_key_for_dict_response(self) -> None:
        # v0.x shape: {"results": [...]}
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            return _mock_response(200, json_data={"results": [{"memory": "fact"}]})

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            results = await client.search("q")
        finally:
            await client.aclose()
        assert results == [{"memory": "fact"}]

    async def test_returns_empty_list_for_empty_response(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            return _mock_response(200, json_data=[])

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            results = await client.search("q")
        finally:
            await client.aclose()
        assert results == []

    async def test_connection_error_translates(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            raise httpx.ConnectError("refused")

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            with pytest.raises(Mem0Error) as exc_info:
                await client.search("q")
        finally:
            await client.aclose()
        assert "unreachable" in str(exc_info.value).lower()

    async def test_4xx_raises_mem0_error_with_body(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            return _mock_response(401, text="not authenticated")

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            with pytest.raises(Mem0Error) as exc_info:
                await client.search("q")
        finally:
            await client.aclose()
        assert "401" in str(exc_info.value)
        assert "not authenticated" in str(exc_info.value)

    async def test_forwards_authorization_header(self) -> None:
        client = Mem0Client(base_url="http://m:8000", authorization="Token secret")
        # Authorization was set at client construction.
        assert client._client.headers.get("authorization") == "Token secret"


@pytest.mark.asyncio
class TestAdd:
    async def test_returns_stored_memories(self) -> None:
        client = Mem0Client(base_url="http://m:8000", user_id="alice")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            assert url.endswith("/v1/memories/")
            assert json["messages"] == [{"role": "user", "content": "fact"}]
            assert json["user_id"] == "alice"
            return _mock_response(200, json_data=[{"id": "x", "memory": "fact"}])

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            stored = await client.add("fact")
        finally:
            await client.aclose()
        assert stored == [{"id": "x", "memory": "fact"}]

    async def test_passes_metadata_through(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")
        captured: dict = {}

        async def fake_post(url, json=None, **kwargs):
            captured.update(json)
            return _mock_response(200, json_data=[])

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            await client.add("text", metadata={"tag": "x"})
        finally:
            await client.aclose()
        assert captured["metadata"] == {"tag": "x"}

    async def test_connection_error_translates(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")

        async def fake_post(url, json=None, **kwargs):
            raise httpx.ConnectError("refused")

        client._client.post = fake_post  # type: ignore[method-assign]
        try:
            with pytest.raises(Mem0Error):
                await client.add("text")
        finally:
            await client.aclose()


@pytest.mark.asyncio
class TestHealth:
    async def test_returns_true_on_200(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")
        client._client.get = AsyncMock(return_value=_mock_response(200))  # type: ignore[method-assign]
        try:
            assert await client.health() is True
        finally:
            await client.aclose()

    async def test_returns_false_on_connection_error(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        client._client = httpx.AsyncClient(base_url="http://m:8000")
        client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))  # type: ignore[method-assign]
        try:
            assert await client.health() is False
        finally:
            await client.aclose()


class TestConstruction:
    def test_base_url_strips_trailing_slash(self) -> None:
        client = Mem0Client(base_url="http://m:8000/")
        assert client.base_url == "http://m:8000"

    def test_no_auth_header_when_authorization_none(self) -> None:
        client = Mem0Client(base_url="http://m:8000", authorization=None)
        assert "authorization" not in client._client.headers
