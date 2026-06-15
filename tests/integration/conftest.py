"""Integration-suite fixtures: stack-up guard and shared clients.

The `mem0_stack_up` fixture is autouse for every test in this directory. It
performs one cheap `/health` request at session start and skips the test
with a helpful message if Mem0 is unreachable. This is the difference
between a test that fails informatively and one that fails confusingly.
"""

from __future__ import annotations

import pytest

from tests.helpers.mem0_client import Mem0Client, Mem0Error


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test under tests/integration/ with @pytest.mark.integration.

    Authors can still add the marker explicitly for clarity, but they don't
    have to. This is what allows `pytest tests/integration` to "just work"
    without each test file repeating the marker.
    """
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def mem0_stack_up(mem0_client: Mem0Client) -> None:
    """Skip the whole integration suite with a clear message if Mem0 is down.

    We hit `/health` rather than `/v1/memories/` because the health endpoint
    doesn't require a user_id and exercises the same network path.
    """
    try:
        mem0_client.health()
    except Mem0Error as exc:
        pytest.skip(
            f"Mem0 stack not reachable at {mem0_client.base_url}: {exc}\n"
            "Start it with `docker compose up -d` and `bash scripts/setup.sh`.",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def mem0_config(mem0_client: Mem0Client) -> dict:
    """The active Mem0 configuration (LLM, embedder, vector store).

    Useful for tests that want to assert on the embedder provider — e.g.
    "the Ollama embedder is configured" — without scraping logs.
    """
    return mem0_client.get_config()
