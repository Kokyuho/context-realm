"""Thin REST client for the Mem0 server used in integration tests.

This intentionally covers only the endpoints the test suite needs. The real
pipeline (pipeline/mem0_filter.py) will eventually grow its own client — for
now, keeping the test client in tests/helpers/ avoids creating a public API
shape we are not yet ready to commit to.

The Mem0 server v1.0.0 exposes unversioned paths (no ``/v1`` prefix). With
auth enabled (``ADMIN_API_KEY`` set) requests must carry
``Authorization: Token <key>``. With ``AUTH_DISABLED=true`` the header is
accepted but ignored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


class Mem0Error(RuntimeError):
    """Raised when the Mem0 server returns a non-2xx response or is unreachable."""


@dataclass
class Mem0Client:
    """Synchronous Mem0 REST client.

    A single instance is created per test session (see tests/conftest.py). It
    is safe to share across tests because httpx.Client is thread-safe and
    each test uses its own isolated user_id.
    """

    base_url: str
    api_key: str | None = None
    timeout: float = 30.0  # local Ollama LLM extraction can take 20s+ on cold start
    add_timeout: float = (
        180.0  # POST /memories: LLM extract + embed + store (cold model load may take 60s+)
    )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        # Allow callers to override the per-request timeout (used by add_memories,
        # which blocks while the LLM extracts facts).
        timeout = kwargs.pop("timeout", self.timeout)
        try:
            response = httpx.request(
                method, url, headers=self._headers(), timeout=timeout, **kwargs
            )
        except httpx.RequestError as exc:
            raise Mem0Error(f"Mem0 unreachable at {url}: {exc}") from exc

        if response.status_code >= 400:
            # Mem0 returns {"detail": "..."} for errors. Surface that body so
            # failing tests show the real reason, not just the status code.
            raise Mem0Error(
                f"Mem0 {method} {path} -> {response.status_code}: {response.text[:500]}"
            )

        if not response.content:
            return None
        return response.json()

    # ─── Health & config ────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        """Liveness probe.

        Mem0 v1 does not expose ``/health``; ``/auth/setup-status`` is a
        cheap, auth-free endpoint that exercises the same network path and
        is sufficient to confirm the server is up.
        """
        return self._request("GET", "/auth/setup-status")

    def get_config(self) -> dict[str, Any]:
        """Returns the current Mem0 configuration (LLM, embedder, vector store, etc.)."""
        return self._request("GET", "/configure")

    # ─── Memories ──────────────────────────────────────────────────────────
    def add_memories(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """POST /memories — extract facts from `messages` and store them.

        Each `messages` entry is `{"role": "user"|"assistant", "content": "..."}`.
        Mem0's LLM extracts discrete facts; the embedder turns them into vectors.
        Returns the list of created memory objects (extracted from Mem0 v1's
        ``{"results": [...]}`` envelope so the test assertions stay linear).
        """
        body: dict[str, Any] = {"messages": messages, "user_id": user_id}
        if metadata:
            body["metadata"] = metadata
        resp = self._request("POST", "/memories", json=body, timeout=self.add_timeout)
        return self._extract_results(resp)

    def list_memories(self, user_id: str) -> list[dict[str, Any]]:
        """GET /memories — list all memories for the user.

        Mem0 v1 stores to Neo4j asynchronously after /memories returns. On a
        slow local Ollama stack the write can lag the response by several
        seconds, so we retry briefly before reporting empty.
        """
        deadline = time.monotonic() + 15.0
        last: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            resp = self._request("GET", "/memories", params={"user_id": user_id})
            last = self._extract_results(resp)
            if last:
                return last
            time.sleep(1.0)
        return last

    def search_memories(self, query: str, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """POST /search — semantic search via the embedder.

        Mem0 v1 rejects top-level ``user_id``/``agent_id``/``run_id`` on
        ``/search`` and requires them inside a ``filters`` dict. Same async-
        write caveat as ``list_memories``: the vector store may not be ready
        when /memories returned. We retry briefly.
        """
        body = {"query": query, "filters": {"user_id": user_id}, "limit": limit}
        deadline = time.monotonic() + 15.0
        last: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            resp = self._request("POST", "/search", json=body, timeout=self.add_timeout)
            last = self._extract_results(resp)
            if last:
                return last
            time.sleep(1.0)
        return last

    def delete_all_memories(self, user_id: str) -> None:
        """Best-effort cleanup. Mem0 returns 200 with an empty body on success."""
        self._request("DELETE", "/memories", params={"user_id": user_id})

    @staticmethod
    def _extract_results(resp: Any) -> list[dict[str, Any]]:
        """Mem0 v1 wraps list responses in ``{"results": [...]}``.

        Older callers pass a plain list (e.g. test fixtures). Normalise both
        shapes to ``list[dict]`` so callers can iterate without branching.
        """
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict) and "results" in resp:
            return list(resp["results"])
        return []
