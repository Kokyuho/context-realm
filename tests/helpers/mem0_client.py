"""Thin REST client for the Mem0 server used in integration tests.

This intentionally covers only the endpoints the test suite needs. The real
pipeline (pipeline/mem0_filter.py) will eventually grow its own client — for
now, keeping the test client in tests/helpers/ avoids creating a public API
shape we are not yet ready to commit to.

The Mem0 server v2.0.2 uses the OpenAI-style /v1/ prefix. With auth enabled
(ADMIN_API_KEY set) requests must carry `Authorization: Token <key>`. With
AUTH_DISABLED=true the header is accepted but ignored.
"""

from __future__ import annotations

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
    timeout: float = 10.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            response = httpx.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
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
        return self._request("GET", "/health")

    def get_config(self) -> dict[str, Any]:
        """Returns the current Mem0 configuration (LLM, embedder, vector store, etc.)."""
        return self._request("GET", "/v1/config")

    # ─── Memories ──────────────────────────────────────────────────────────
    def add_memories(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """POST /v1/memories — extract facts from `messages` and store them.

        Each `messages` entry is `{"role": "user"|"assistant", "content": "..."}`.
        Mem0's LLM extracts discrete facts; the embedder turns them into vectors.
        Returns the list of created memory objects.
        """
        body: dict[str, Any] = {"messages": messages, "user_id": user_id}
        if metadata:
            body["metadata"] = metadata
        return self._request("POST", "/v1/memories/", json=body)

    def list_memories(self, user_id: str) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/memories/", params={"user_id": user_id})

    def search_memories(self, query: str, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """POST /v1/memories/search — semantic search via the embedder."""
        return self._request(
            "POST",
            "/v1/memories/search/",
            json={"query": query, "user_id": user_id, "limit": limit},
        )

    def delete_all_memories(self, user_id: str) -> None:
        """Best-effort cleanup. Mem0 returns 200 with an empty body on success."""
        self._request("DELETE", "/v1/memories/", params={"user_id": user_id})
