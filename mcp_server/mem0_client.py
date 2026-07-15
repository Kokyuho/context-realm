"""Async Mem0 REST client used by the MCP server.

Kept deliberately small — the MCP server only needs two operations:
``search`` and ``add``. The full Mem0 surface (list, get, delete, history)
is out of scope for v1; users who need those can hit Mem0 directly.

The client forwards whatever ``Authorization`` header it was constructed
with, so the MCP server's token-auth middleware can present the same
credentials to Mem0 that the client presented to the MCP server.
"""

from __future__ import annotations

from typing import Any

import httpx


class Mem0Error(RuntimeError):
    """Raised when Mem0 returns a non-2xx response or is unreachable."""


class Mem0Client:
    """Minimal async Mem0 client. One shared client per process."""

    def __init__(
        self,
        base_url: str,
        authorization: str | None = None,
        user_id: str = "default",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": authorization} if authorization else {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        """Return True iff Mem0 is up and responding.

        Mem0 v1 doesn't expose ``/health``; ``/auth/setup-status`` is a cheap,
        auth-free endpoint that exercises the same network path.
        """
        try:
            r = await self._client.get("/auth/setup-status")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """POST /v1/memories/search/ — semantic search via Mem0's embedder.

        Returns the raw list of memory objects. Mem0's response shape
        varies between versions (``{"results": [...]}`` on v0.x, ``[...]``
        on v2.x); we normalise to a plain list.
        """
        payload = {"query": query, "user_id": self.user_id, "limit": limit}
        try:
            r = await self._client.post("/v1/memories/search/", json=payload)
        except httpx.HTTPError as exc:
            raise Mem0Error(f"Mem0 unreachable at {self.base_url}: {exc}") from exc
        if r.status_code >= 400:
            raise Mem0Error(f"Mem0 search -> {r.status_code}: {r.text[:500]}")
        data = r.json() if r.content else []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results = data.get("results") or data.get("memories") or []
            return results if isinstance(results, list) else []
        return []

    async def add(self, text: str, metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """POST /v1/memories/ — feed a single fact through Mem0's extractor.

        Mem0 runs the text through its fact-extraction LLM, embeds the
        resulting facts, and stores them. Returns the list of stored
        memory objects.
        """
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "user_id": self.user_id,
        }
        if metadata:
            payload["metadata"] = metadata
        try:
            r = await self._client.post("/v1/memories/", json=payload)
        except httpx.HTTPError as exc:
            raise Mem0Error(f"Mem0 unreachable at {self.base_url}: {exc}") from exc
        if r.status_code >= 400:
            raise Mem0Error(f"Mem0 add -> {r.status_code}: {r.text[:500]}")
        data = r.json() if r.content else []
        return data if isinstance(data, list) else []
