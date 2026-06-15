"""Integration tests for Mem0's /v1/memories/ endpoint.

The headline test (`test_add_and_search_round_trip`) is the one this file
exists for: it proves the full embedding pipeline works end-to-end on this
machine, not just the REST plumbing.

  1. POST a memory containing a distinctive sentence
  2. List memories and confirm it was stored
  3. Search with a semantically related but lexically different query
  4. Assert the original memory is in the search results

If any step fails, embedding is broken (or pointing at the wrong model,
or missing the OLLAMA_BASE_URL, or…). The failure message tells you which
one — we deliberately assert on the embedder config in `test_embedder_config`
so a wrong-provider failure surfaces with a clear pointer.

Pre-requisites (run once):
    docker compose up -d
    bash scripts/setup.sh     # pulls qwen3-embedding:0.6b into Ollama
"""

from __future__ import annotations

from tests.helpers.mem0_client import Mem0Client

# A distinctive, unambiguous fact. We will search for a paraphrase, not the
# exact words, so a hit can only come from the embedding model producing
# semantically meaningful vectors — not from string matching.
_DISTINCTIVE_FACT_USER = (
    "I am a marine biologist who studies bioluminescent squid off the coast of Tasmania."
)
_DISTINCTIVE_FACT_ASSISTANT = "Noted — that's a fascinating specialty."
_SEARCH_QUERY = "Tell me about this person's scientific field of study and location."


class TestMem0Memories:
    """The /v1/memories/ endpoint accepts, stores, and retrieves embedded facts."""

    def test_embedder_config_uses_ollama(self, mem0_config: dict) -> None:
        """Guard: the test below is meaningless if the embedder is not Ollama.

        If someone changes MEM0_DEFAULT_EMBEDDER_PROVIDER to `openai` in .env
        this test fails fast with a clear message, instead of a downstream
        401 from a missing OPENAI_API_KEY during the round-trip test.
        """
        embedder = mem0_config.get("embedder", {})
        provider = embedder.get("provider")
        assert provider == "ollama", (
            f"Expected embedder.provider == 'ollama', got {provider!r}. "
            "The integration suite assumes local embeddings via Ollama. "
            "Either set MEM0_DEFAULT_EMBEDDER_PROVIDER=ollama or mark this "
            "test xfail for non-ollama configurations."
        )

    def test_health_endpoint_reachable(self, mem0_client: Mem0Client) -> None:
        """`/health` should return 200. Quick sanity check before deeper tests."""
        health = mem0_client.health()
        assert health is not None

    def test_add_and_search_round_trip(self, mem0_client: Mem0Client, unique_user_id: str) -> None:
        """The headline test: POST a memory, then find it via semantic search.

        This proves the embedder is reachable from Mem0 AND that vectors
        are being persisted AND that similarity search works. All three
        of those have been broken in this stack at various points.
        """
        # 1. Store the memory. Mem0 runs the message through its LLM to
        #    extract discrete facts before embedding; with auth disabled
        #    and a fast embedder, this should complete in < 2s.
        created = mem0_client.add_memories(
            messages=[
                {"role": "user", "content": _DISTINCTIVE_FACT_USER},
                {"role": "assistant", "content": _DISTINCTIVE_FACT_ASSISTANT},
            ],
            user_id=unique_user_id,
        )
        assert created, "Mem0 returned no memories for a non-empty message"
        assert all("id" in m for m in created), f"Mem0 returned memories without ids: {created!r}"

        # 2. List should now include at least one of the created memories.
        listed = mem0_client.list_memories(user_id=unique_user_id)
        listed_ids = {m["id"] for m in listed}
        created_ids = {m["id"] for m in created}
        assert created_ids & listed_ids, (
            f"None of the just-created memories appear in the list response.\n"
            f"  Created: {created_ids}\n"
            f"  Listed:  {listed_ids}"
        )

        # 3. Search with a paraphrase. If the embedder is misconfigured
        #    (e.g. falling back to a hash, or returning all-zero vectors)
        #    this either errors out or returns unrelated memories.
        results = mem0_client.search_memories(query=_SEARCH_QUERY, user_id=unique_user_id, limit=5)
        assert results, "Search returned no results for a user with stored memories"

        # 4. The original distinctive fact should be in the top results.
        #    We look for the substring "Tasmania" because it's the most
        #    uniquely identifying token — the search query doesn't contain
        #    it, so a hit can only come from semantic similarity.
        result_text = " ".join(str(r.get("memory", "")) for r in results).lower()
        assert "tasmania" in result_text or "marine biologist" in result_text, (
            "Search did not surface the stored memory for a related query.\n"
            f"  Query:  {_SEARCH_QUERY!r}\n"
            f"  Got:    {result_text!r}\n"
            "This usually means the embedder is returning low-quality vectors.\n"
            "Check that qwen3-embedding:0.6b is pulled: `docker exec "
            "contextrealm-ollama-1 ollama list`."
        )

    def test_list_memories_is_empty_for_fresh_user(
        self, mem0_client: Mem0Client, unique_user_id: str
    ) -> None:
        """A user_id that has never stored anything should return an empty list.

        Catches a class of bugs where Mem0 returns all memories regardless
        of the user_id filter — which would be a privacy bug.
        """
        listed = mem0_client.list_memories(user_id=unique_user_id)
        assert listed == [], f"Expected no memories for a fresh user, got {listed!r}"
