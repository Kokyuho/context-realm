"""
ContextRealm — mem0_filter pipeline
====================================

The single piece of custom logic in the stack. Lives inside the Open WebUI
Pipelines service and is invoked on every chat exchange:

  inlet(body, user)   — run BEFORE the request reaches the model.
                        Search Mem0 for relevant memories and prepend them
                        to the system prompt so the model can answer in
                        context.

  outlet(body, user)  — run AFTER the model has responded.
                        Store the just-completed turn in Mem0 so it can be
                        retrieved on future exchanges.

Failure handling
----------------
Both directions fail gracefully. If Mem0 is unreachable, slow, or returns a
4xx/5xx, the pipeline swallows the exception and returns the body unchanged.
The user's chat must never be blocked by a memory error.

Auth
----
Mem0 v2.0.2+ reads the admin API key from the ``ADMIN_API_KEY`` env var and
expects ``Authorization: Token <key>`` on all REST calls. With
``AUTH_DISABLED=true`` (local dev) the header is accepted but ignored.
We always send it when present so the same image works in both modes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("contextrealm.mem0_filter")

# Conservative ceilings — the chat UX is more important than memory fidelity.
# If Mem0 takes longer than this the user has already seen the "thinking"
# spinner; better to ship a response than to wait.
_SEARCH_TIMEOUT_S = 3.0
_STORE_TIMEOUT_S = 5.0


class Pipeline:
    """Open WebUI Pipelines filter: pre/post hooks around every LLM call."""

    class Valves(BaseModel):
        """Operator-configurable settings. Exposed in the Pipelines admin UI."""

        enabled: bool = Field(
            default=True,
            description="Master switch. When false, the pipeline is a pass-through.",
        )
        mem0_api_url: str = Field(
            default="http://mem0:8000",
            description="Base URL of the Mem0 REST server.",
        )
        mem0_api_key: str = Field(
            default="",
            description=(
                "Mem0 admin API key (matches ADMIN_API_KEY in .env). "
                "Leave blank when MEM0_AUTH_DISABLED=true."
            ),
        )
        mem0_user_id: str = Field(
            default="default",
            description=(
                "User ID used when the request has no authenticated user. "
                "In multi-tenant setups, set per-realm or per-session."
            ),
        )
        max_memories: int = Field(
            default=10,
            description="Maximum number of memories to inject per turn.",
            ge=1,
            le=50,
        )
        memory_label: str = Field(
            default="Relevant memories from your Realm",
            description=(
                "Header line printed before the injected memory block. "
                "Helps the model distinguish recall from instructions."
            ),
        )
        include_assistant_turn: bool = Field(
            default=True,
            description=(
                "If true, the outlet stores the last user+assistant turn. "
                "Disable to only store user messages (denser, less context)."
            ),
        )

    def __init__(self) -> None:
        self.name = "ContextRealm Mem0 Filter"
        self.valves = self.Valves()
        # Resolve env at construction time so a container restart picks up
        # new values without code changes. The Valve still wins in the UI.
        self.valves.mem0_api_url = os.environ.get("MEM0_API_URL", self.valves.mem0_api_url)
        self.valves.mem0_api_key = os.environ.get("MEM0_API_KEY", self.valves.mem0_api_key)
        self.valves.mem0_user_id = os.environ.get("MEM0_USER_ID", self.valves.mem0_user_id)
        # Single shared client. httpx recommends connection pooling for
        # repeated calls to the same host, which is exactly our pattern.
        self._client: httpx.AsyncClient | None = None

    # ─── Lifecycle hooks (called by the Pipelines framework) ─────────────────
    async def on_startup(self) -> None:
        """Verify Mem0 is reachable before serving the first request."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_SEARCH_TIMEOUT_S, connect=2.0),
            headers=self._headers(),
        )
        try:
            r = await self._client.get(f"{self.valves.mem0_api_url}/health")
            r.raise_for_status()
            logger.info("mem0_filter connected to %s", self.valves.mem0_api_url)
        except httpx.HTTPError as exc:
            # Don't crash the Pipelines service on a single bad connection —
            # we'll keep trying per-request and log a warning each time.
            logger.warning(
                "mem0_filter startup: Mem0 unreachable at %s (%s). "
                "The pipeline will keep running and retry on each request.",
                self.valves.mem0_api_url,
                exc,
            )

    async def on_shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ─── Inlet: inject memories before the model sees the request ────────────
    async def inlet(self, body: dict, user: dict | None = None) -> dict:
        """Search Mem0 for memories relevant to the latest user message."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages") or []
        if not messages:
            return body

        last_user_text = _extract_last_user_text(messages)
        if not last_user_text:
            return body

        user_id = _resolve_user_id(user, self.valves.mem0_user_id)
        memories = await self._search_memories(last_user_text, user_id)
        if not memories:
            return body

        body = self._inject_memory_context(body, memories)
        return body

    # ─── Outlet: store the completed turn in Mem0 ────────────────────────────
    async def outlet(self, body: dict, user: dict | None = None) -> dict:
        """Send the last user+assistant turn to Mem0 for future recall."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages") or []
        if not messages:
            return body

        turn = _extract_completed_turn(
            messages, include_assistant=self.valves.include_assistant_turn
        )
        if not turn:
            return body

        user_id = _resolve_user_id(user, self.valves.mem0_user_id)
        await self._store_memories(turn, user_id)
        return body

    # ─── Mem0 REST helpers ───────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.valves.mem0_api_key:
            headers["Authorization"] = f"Token {self.valves.mem0_api_key}"
        return headers

    async def _search_memories(self, query: str, user_id: str) -> list[dict[str, Any]]:
        """Return up to ``max_memories`` memories for ``query``; [] on any error."""
        url = f"{self.valves.mem0_api_url.rstrip('/')}/v1/memories/search/"
        payload = {
            "query": query,
            "user_id": user_id,
            "limit": self.valves.max_memories,
        }
        try:
            client = self._require_client()
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as exc:
            logger.warning("mem0 search failed: %s", exc)
            return []
        except (ValueError, TypeError) as exc:
            # Mem0 occasionally returns a non-JSON body on transient errors.
            logger.warning("mem0 search returned unparseable body: %s", exc)
            return []

        # Mem0 returns {"results": [...]} on v0.x and [...] on v2.x.
        # Normalise so the rest of the pipeline doesn't care.
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results = data.get("results") or data.get("memories") or []
            return results if isinstance(results, list) else []
        return []

    async def _store_memories(self, messages: list[dict[str, str]], user_id: str) -> None:
        """POST the turn to Mem0. Silently swallows all errors."""
        url = f"{self.valves.mem0_api_url.rstrip('/')}/v1/memories/"
        payload: dict[str, Any] = {"messages": messages, "user_id": user_id}
        try:
            client = self._require_client()
            r = await client.post(
                url,
                json=payload,
                timeout=httpx.Timeout(_STORE_TIMEOUT_S, connect=2.0),
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            # Don't surface this to the user — Mem0 might just be restarting.
            logger.warning("mem0 store failed: %s", exc)

    # ─── Body manipulation helpers (pure functions, easy to unit-test) ───────
    def _inject_memory_context(self, body: dict, memories: list[dict[str, Any]]) -> dict:
        """Prepend a system message containing the formatted memories.

        Mem0 entries come back in many shapes depending on the upstream
        version and the response of the fact-extraction LLM. The fields we
        try, in order, are: ``memory`` → ``text`` → ``content`` → str(dict).
        The score is shown in parentheses when present so the model can
        prefer higher-confidence recalls.
        """
        lines: list[str] = []
        for m in memories:
            text = _memory_text(m)
            if not text:
                continue
            score = m.get("score")
            if isinstance(score, (int, float)):
                lines.append(f"- ({score:.2f}) {text}")
            else:
                lines.append(f"- {text}")

        if not lines:
            return body

        header = self.valves.memory_label
        block = f"{header}:\n" + "\n".join(lines)
        system_msg = {"role": "system", "content": block}

        # Open WebUI hands us an OpenAI-style body: messages[0] is often
        # already a system message. Insert our memories right after it so
        # we don't push user-defined instructions to position 2.
        existing = list(body.get("messages") or [])
        if existing and existing[0].get("role") == "system":
            existing.insert(1, system_msg)
        else:
            existing.insert(0, system_msg)
        body["messages"] = existing
        return body

    def _require_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating one if on_startup didn't run.

        The Pipelines framework calls on_startup before serving traffic, so
        this branch is only hit in tests and unusual embedding contexts.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self._headers())
        return self._client


# ─── Module-level helpers (pure, no I/O — easy to unit test) ──────────────────


def _extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the content of the most recent user-role message, or '' if none.

    Open WebUI sends content as either a plain string or a list of
    multimodal parts (``[{"type": "text", "text": "..."}, ...]``). We
    concatenate the text parts to handle both shapes uniformly.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p)
    return ""


def _extract_completed_turn(
    messages: list[dict[str, Any]],
    *,
    include_assistant: bool,
) -> list[dict[str, str]]:
    """Return the trailing [user, assistant] (or just [user]) messages to store.

    The outlet runs AFTER the model responds, so the last message in the
    list is the freshly-generated assistant reply. We hand the last two
    messages to Mem0 so it has both sides of the turn for fact extraction.

    Skips the turn entirely if we don't see a user message, since storing
    assistant-only turns has no useful signal.
    """
    if not messages:
        return []

    if include_assistant and len(messages) >= 2:
        last_two = messages[-2:]
        if last_two[0].get("role") == "user" and last_two[-1].get("role") == "assistant":
            return [
                {"role": "user", "content": _stringify(last_two[0].get("content"))},
                {
                    "role": "assistant",
                    "content": _stringify(last_two[-1].get("content")),
                },
            ]

    # Fallback: store the last user message we can find.
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return [{"role": "user", "content": _stringify(msg.get("content"))}]
    return []


def _stringify(content: Any) -> str:
    """Coerce multimodal content to a plain string for Mem0."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _memory_text(memory: dict[str, Any]) -> str:
    """Pull the human-readable text out of a Mem0 memory object."""
    for key in ("memory", "text", "content"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Last resort: the whole dict serialised. Rarely pretty, but never empty.
    return str(memory).strip()


def _resolve_user_id(user: dict | None, default: str) -> str:
    """Pick the best user_id available — authenticated user wins, then env, then default."""
    if isinstance(user, dict):
        candidate = user.get("id") or user.get("email")
        if isinstance(candidate, str) and candidate:
            return candidate
    return default
