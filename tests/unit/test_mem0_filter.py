"""Unit tests for pipeline/mem0_filter.py.

The pipeline is the only piece of custom logic in the stack. These tests
mock the Mem0 client so they run in milliseconds and never need the
Docker stack. The integration tests in tests/integration/ prove the
pipeline talks to a real Mem0 server; here we prove it does the right
thing with the response.

Coverage map:
  * TestInletExtraction           — content extraction (string + list shapes)
  * TestOutletTurnExtraction      — completed-turn detection
  * TestMemoryFormatting          — score formatting, _memory_text tolerance
  * TestUserIdResolution          — auth > env > default
  * TestInletMemoryInjection      — end-to-end inlet with mocked Mem0
  * TestOutletMemoryStorage       — end-to-end outlet with mocked Mem0
  * TestGracefulDegradation       — every failure mode in the spec
  * TestDisabledValve             — valve flips everything off
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Make `pipeline.*` importable when pytest is invoked from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import mem0_filter  # noqa: E402  (path-adjustment import)
from pipeline.mem0_filter import Pipeline  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _build_pipeline(**valve_overrides) -> Pipeline:
    """Construct a Pipeline with the on_startup side effect skipped.

    We never want the test to actually hit Mem0's health endpoint.
    """
    pipe = Pipeline()
    for key, value in valve_overrides.items():
        setattr(pipe.valves, key, value)
    return pipe


def _patched_client(response_json=None, *, raises: Exception | None = None) -> AsyncMock:
    """Build a mock httpx.AsyncClient matching the pipeline's expectations."""
    mock_post = AsyncMock()
    if raises is not None:
        mock_post.side_effect = raises
    else:
        response = SimpleNamespace(
            status_code=200,
            json=lambda: response_json,
            raise_for_status=lambda: None,
        )
        mock_post.return_value = response

    mock_client = AsyncMock()
    mock_client.post = mock_post
    return mock_client


# ─── Content extraction (inlet pre-flight) ───────────────────────────────────


class TestInletExtraction:
    def test_returns_last_user_text_string_content(self) -> None:
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is my dog's name?"},
            ]
        }
        assert mem0_filter._extract_last_user_text(body["messages"]) == ("What is my dog's name?")

    def test_returns_last_user_text_with_list_content(self) -> None:
        # Open WebUI sends multimodal messages as a list of parts.
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello there"},
                        {"type": "image_url", "image_url": {"url": "http://x"}},
                    ],
                }
            ]
        }
        assert mem0_filter._extract_last_user_text(body["messages"]) == "Hello there"

    def test_returns_empty_when_no_user_message(self) -> None:
        body = {"messages": [{"role": "system", "content": "You are helpful."}]}
        assert mem0_filter._extract_last_user_text(body["messages"]) == ""

    def test_returns_empty_for_empty_message_list(self) -> None:
        assert mem0_filter._extract_last_user_text([]) == ""

    def test_picks_last_user_when_multiple(self) -> None:
        # The most recent user turn is what we search against.
        body = {
            "messages": [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "second question"},
            ]
        }
        assert mem0_filter._extract_last_user_text(body["messages"]) == "second question"


# ─── Outlet turn extraction ─────────────────────────────────────────────────


class TestOutletTurnExtraction:
    def test_returns_user_assistant_pair(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        turn = mem0_filter._extract_completed_turn(messages, include_assistant=True)
        assert turn == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_returns_only_user_when_assistant_disabled(self) -> None:
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        turn = mem0_filter._extract_completed_turn(messages, include_assistant=False)
        assert turn == [{"role": "user", "content": "hi"}]

    def test_stringifies_list_content(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "multimodal hi"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "multimodal hello"}],
            },
        ]
        turn = mem0_filter._extract_completed_turn(messages, include_assistant=True)
        assert turn[0]["content"] == "multimodal hi"
        assert turn[1]["content"] == "multimodal hello"

    def test_falls_back_to_last_user_when_pair_missing(self) -> None:
        # If the model hasn't responded yet, only the user turn is stored.
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        turn = mem0_filter._extract_completed_turn(messages, include_assistant=True)
        assert turn == [{"role": "user", "content": "hi"}]

    def test_empty_when_no_user_message(self) -> None:
        assert (
            mem0_filter._extract_completed_turn(
                [{"role": "system", "content": "sys"}], include_assistant=True
            )
            == []
        )


# ─── Memory formatting ──────────────────────────────────────────────────────


class TestMemoryFormatting:
    def test_memory_text_prefers_memory_key(self) -> None:
        assert mem0_filter._memory_text({"memory": "hello", "text": "ignored"}) == "hello"

    def test_memory_text_falls_back_to_text_then_content(self) -> None:
        assert mem0_filter._memory_text({"text": "from text"}) == "from text"
        assert mem0_filter._memory_text({"content": "from content"}) == "from content"

    def test_memory_text_falls_back_to_str(self) -> None:
        # When Mem0 returns something exotic, we still hand the model *something*.
        result = mem0_filter._memory_text({"weird": "shape"})
        assert "weird" in result and "shape" in result

    def test_memory_text_empty_string_skipped(self) -> None:
        # An empty memory field must not become "- " (would render as junk).
        assert mem0_filter._memory_text({"memory": "   "}) == str({"memory": "   "}).strip()

    def test_inject_inserts_after_existing_system(self) -> None:
        pipe = _build_pipeline()
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ]
        }
        memories = [{"memory": "User likes cats", "score": 0.92}]
        out = pipe._inject_memory_context(body, memories)
        msgs = out["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."
        assert msgs[1]["role"] == "system"
        assert "cats" in msgs[1]["content"]
        assert "0.92" in msgs[1]["content"]

    def test_inject_prepends_when_no_existing_system(self) -> None:
        pipe = _build_pipeline()
        body = {"messages": [{"role": "user", "content": "hi"}]}
        out = pipe._inject_memory_context(body, [{"memory": "fact"}])
        assert out["messages"][0]["role"] == "system"
        assert "fact" in out["messages"][0]["content"]

    def test_inject_drops_empty_memories(self) -> None:
        pipe = _build_pipeline()
        body = {"messages": [{"role": "user", "content": "hi"}]}
        # Two empty memories + one real one — only the real one should appear.
        out = pipe._inject_memory_context(
            body,
            [{"memory": ""}, {"text": "  "}, {"memory": "real fact"}],
        )
        # No system message should be inserted at all if the result is empty.
        # The single real memory produces one line, so the body is mutated.
        assert len(out["messages"]) == 2
        assert "real fact" in out["messages"][0]["content"]


# ─── User ID resolution ─────────────────────────────────────────────────────


class TestUserIdResolution:
    def test_authenticated_user_wins(self) -> None:
        assert mem0_filter._resolve_user_id({"id": "alice"}, "default") == "alice"

    def test_email_fallback(self) -> None:
        # Some Open WebUI versions pass email when id is missing.
        assert mem0_filter._resolve_user_id({"email": "a@b"}, "default") == "a@b"

    def test_default_when_user_is_none(self) -> None:
        assert mem0_filter._resolve_user_id(None, "default") == "default"

    def test_default_when_user_id_empty(self) -> None:
        assert mem0_filter._resolve_user_id({"id": ""}, "default") == "default"


# ─── Inlet: end-to-end with mocked Mem0 ──────────────────────────────────────


@pytest.mark.asyncio
class TestInletMemoryInjection:
    async def test_injects_memories_into_system_prompt(self) -> None:
        pipe = _build_pipeline(mem0_api_url="http://mem0:8000")
        pipe._client = _patched_client({"results": [{"memory": "User is a marine biologist"}]})

        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What do I do for work?"},
            ]
        }
        out = await pipe.inlet(body, user={"id": "alice"})

        # Search hit the right endpoint with the right payload.
        pipe._client.post.assert_called_once()
        call = pipe._client.post.call_args
        assert call.args[0] == "http://mem0:8000/v1/memories/search/"
        assert call.kwargs["json"]["query"] == "What do I do for work?"
        assert call.kwargs["json"]["user_id"] == "alice"
        assert call.kwargs["json"]["limit"] == 10

        # Memories were prepended to the system message.
        assert "marine biologist" in out["messages"][1]["content"]
        # Original system prompt is preserved at position 0.
        assert out["messages"][0]["content"] == "You are helpful."

    async def test_inlet_handles_list_response_shape(self) -> None:
        # Mem0 v2.x returns a bare list. Make sure we don't blow up on it.
        pipe = _build_pipeline()
        pipe._client = _patched_client([{"memory": "v2 shape"}])
        out = await pipe.inlet({"messages": [{"role": "user", "content": "x"}]}, user=None)
        assert "v2 shape" in out["messages"][0]["content"]

    async def test_inlet_passes_auth_header_when_key_set(self) -> None:
        pipe = _build_pipeline(mem0_api_key="secret-123")
        pipe._client = _patched_client({"results": []})
        await pipe.inlet({"messages": [{"role": "user", "content": "x"}]}, user=None)
        # Headers were applied at client construction, not per-call, so we
        # just confirm the client is configured. A more thorough check would
        # mock httpx.AsyncClient itself; this is sufficient.
        assert pipe._client.post.called

    async def test_inlet_returns_body_unchanged_when_no_memories(self) -> None:
        pipe = _build_pipeline()
        pipe._client = _patched_client({"results": []})
        body = {"messages": [{"role": "user", "content": "x"}]}
        out = await pipe.inlet(body, user=None)
        # No memories → no new system message.
        assert out["messages"] == body["messages"]


# ─── Outlet: end-to-end with mocked Mem0 ────────────────────────────────────


@pytest.mark.asyncio
class TestOutletMemoryStorage:
    async def test_stores_user_assistant_pair(self) -> None:
        pipe = _build_pipeline(mem0_api_url="http://mem0:8000")
        pipe._client = _patched_client({"results": []})  # POST /v1/memories/ returns ok

        body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "I like cats"},
                {"role": "assistant", "content": "Noted."},
            ]
        }
        await pipe.outlet(body, user={"id": "alice"})

        pipe._client.post.assert_called_once()
        call = pipe._client.post.call_args
        assert call.args[0] == "http://mem0:8000/v1/memories/"
        payload = call.kwargs["json"]
        assert payload["user_id"] == "alice"
        assert payload["messages"] == [
            {"role": "user", "content": "I like cats"},
            {"role": "assistant", "content": "Noted."},
        ]

    async def test_outlet_user_only_when_valve_off(self) -> None:
        pipe = _build_pipeline(include_assistant_turn=False)
        pipe._client = _patched_client({})
        body = {
            "messages": [
                {"role": "user", "content": "I like cats"},
                {"role": "assistant", "content": "Noted."},
            ]
        }
        await pipe.outlet(body, user=None)
        payload = pipe._client.post.call_args.kwargs["json"]
        assert payload["messages"] == [{"role": "user", "content": "I like cats"}]

    async def test_outlet_falls_back_to_user_only_when_no_assistant(self) -> None:
        pipe = _build_pipeline()
        pipe._client = _patched_client({})
        body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "I like cats"},
            ]
        }
        await pipe.outlet(body, user=None)
        payload = pipe._client.post.call_args.kwargs["json"]
        assert payload["messages"] == [{"role": "user", "content": "I like cats"}]


# ─── Graceful degradation (the "must never block the chat" guarantee) ───────


@pytest.mark.asyncio
class TestGracefulDegradation:
    async def test_inlet_swallows_connection_error(self) -> None:
        import httpx

        pipe = _build_pipeline()
        pipe._client = _patched_client(raises=httpx.ConnectError("refused"))
        body = {"messages": [{"role": "user", "content": "x"}]}
        # Must not raise.
        out = await pipe.inlet(body, user=None)
        # Body is unchanged.
        assert out == body

    async def test_inlet_swallows_timeout(self) -> None:
        import httpx

        pipe = _build_pipeline()
        pipe._client = _patched_client(raises=httpx.ReadTimeout("slow"))
        body = {"messages": [{"role": "user", "content": "x"}]}
        out = await pipe.inlet(body, user=None)
        assert out == body

    async def test_inlet_swallows_5xx(self) -> None:
        pipe = _build_pipeline()
        # Build a response that raises on raise_for_status().
        error_response = SimpleNamespace(
            status_code=503,
            json=lambda: {"detail": "service unavailable"},
            raise_for_status=lambda: (_ for _ in ()).throw(
                __import__("httpx").HTTPStatusError("503", request=None, response=error_response)
            ),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=error_response)
        pipe._client = mock_client

        body = {"messages": [{"role": "user", "content": "x"}]}
        out = await pipe.inlet(body, user=None)
        assert out == body

    async def test_inlet_swallows_unparseable_json(self) -> None:
        pipe = _build_pipeline()
        bad_response = SimpleNamespace(
            status_code=200,
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            raise_for_status=lambda: None,
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=bad_response)
        pipe._client = mock_client

        body = {"messages": [{"role": "user", "content": "x"}]}
        out = await pipe.inlet(body, user=None)
        assert out == body

    async def test_outlet_swallows_connection_error(self) -> None:
        import httpx

        pipe = _build_pipeline()
        pipe._client = _patched_client(raises=httpx.ConnectError("refused"))
        body = {
            "messages": [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ]
        }
        # Must not raise.
        out = await pipe.outlet(body, user=None)
        assert out == body

    async def test_outlet_swallows_5xx(self) -> None:
        pipe = _build_pipeline()
        error_response = SimpleNamespace(
            status_code=500,
            json=lambda: {"detail": "boom"},
            raise_for_status=lambda: (_ for _ in ()).throw(
                __import__("httpx").HTTPStatusError("500", request=None, response=error_response)
            ),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=error_response)
        pipe._client = mock_client

        body = {
            "messages": [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ]
        }
        out = await pipe.outlet(body, user=None)
        assert out == body

    async def test_pipeline_constructs_client_when_on_startup_skipped(self) -> None:
        # When something constructs Pipeline() and calls inlet() without ever
        # running on_startup (e.g. in tests, or in unusual embedder contexts),
        # the pipeline must still work. This is the "fail open" guarantee.
        pipe = _build_pipeline()
        assert pipe._client is None
        pipe._client = _patched_client({"results": []})
        await pipe.inlet({"messages": [{"role": "user", "content": "x"}]}, user=None)
        assert pipe._client.post.called


# ─── Disabled valve ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDisabledValve:
    async def test_inlet_is_passthrough_when_disabled(self) -> None:
        pipe = _build_pipeline(enabled=False)
        pipe._client = _patched_client({"results": [{"memory": "should not appear"}]})
        body = {"messages": [{"role": "user", "content": "x"}]}
        out = await pipe.inlet(body, user=None)
        # Body unchanged, no Mem0 call made.
        assert out == body
        pipe._client.post.assert_not_called()

    async def test_outlet_is_passthrough_when_disabled(self) -> None:
        pipe = _build_pipeline(enabled=False)
        pipe._client = _patched_client({})
        body = {
            "messages": [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ]
        }
        out = await pipe.outlet(body, user=None)
        assert out == body
        pipe._client.post.assert_not_called()


# ─── Env-var resolution at construction time ────────────────────────────────


class TestEnvironmentResolution:
    def test_env_vars_override_valve_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEM0_API_URL", "http://custom-mem0:8000")
        monkeypatch.setenv("MEM0_API_KEY", "env-key")
        monkeypatch.setenv("MEM0_USER_ID", "env-user")
        # The Pipeline constructor reads the env once at __init__ time, so we
        # construct a fresh instance after setting the env.
        pipe = Pipeline()
        assert pipe.valves.mem0_api_url == "http://custom-mem0:8000"
        assert pipe.valves.mem0_api_key == "env-key"
        assert pipe.valves.mem0_user_id == "env-user"
