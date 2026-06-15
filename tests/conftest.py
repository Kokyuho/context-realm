"""Root test configuration and shared fixtures.

Responsibilities:
  1. Load .env into os.environ once per session, so tests and helpers can use
     the same values the running stack uses.
  2. Provide service-URL and auth-key fixtures that the integration suite reads.
  3. Register the asyncio mode for pytest-asyncio (used by pipeline tests).
"""

from __future__ import annotations

import os
import pathlib
import uuid
from collections.abc import Iterator

import pytest

# ─── .env loading ────────────────────────────────────────────────────────────
# Tests run on the developer's host and in CI. In both cases, the only place
# the URL of the Mem0 server and the API key live is the .env file at the
# repo root. We load it lazily here so unit tests that don't need any of
# these values still work in a clean checkout with no .env present.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_ROOT / ".env"


def _load_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Don't clobber values already set in the actual environment — the
        # shell wins. .env is the fallback, not the override.
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(_ENV_PATH)


# ─── pytest-asyncio configuration ────────────────────────────────────────────
# `auto` mode would force every `async def test_*` to be treated as async; that
# is rarely what you want in a mixed suite. The default `strict` mode requires
# an explicit `@pytest.mark.asyncio` marker, which is clearer at the call site.
# Pipeline tests can opt in once Phase 5 lands and they become async.


# ─── Service fixtures ────────────────────────────────────────────────────────
# These are session-scoped because the values come from the environment and
# don't change between tests. Each test that hits Mem0 should still use a
# unique user_id (see `unique_user_id`) so tests don't see each other's data.


@pytest.fixture(scope="session")
def mem0_base_url() -> str:
    """URL of the Mem0 server as reachable from the test runner.

    On the host machine this is `http://localhost:8000`. From inside the
    `pipelines` container it would be `http://mem0:8000`. Tests always run
    on the host, so we default to localhost and allow override via env.
    """
    return os.environ.get("MEM0_TEST_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def mem0_api_key() -> str | None:
    """Admin API key for Mem0. None when MEM0_AUTH_DISABLED=true."""
    return os.environ.get("MEM0_ADMIN_API_KEY") or None


@pytest.fixture(scope="session")
def mem0_client(mem0_base_url: str, mem0_api_key: str | None):
    """A single Mem0Client for the whole test session."""
    # Imported lazily so unit tests don't pull httpx's import cost.
    from tests.helpers.mem0_client import Mem0Client

    return Mem0Client(base_url=mem0_base_url, api_key=mem0_api_key)


@pytest.fixture
def unique_user_id() -> Iterator[str]:
    """Yield a user_id unique to this test, then clean up the user's memories.

    Tests that store memories MUST go through this fixture. It guarantees:
      * No cross-test contamination: every test sees an empty namespace.
      * No leftover state in the developer's Mem0 database after a test run.
    """
    from tests.helpers.mem0_client import Mem0Error

    user_id = f"test-{uuid.uuid4().hex[:12]}"
    yield user_id
    # Best-effort cleanup. We warn rather than raise or skip so the test's
    # own outcome is what pytest reports. (Using pytest.skip here would mask
    # a passing test as skipped whenever the stack is down.)
    try:
        # Re-create the client from env because the session fixture is not
        # available in the finaliser (depends on fixture lifetime).
        from tests.helpers.mem0_client import Mem0Client

        client = Mem0Client(
            base_url=os.environ.get("MEM0_TEST_BASE_URL", "http://localhost:8000"),
            api_key=os.environ.get("MEM0_ADMIN_API_KEY") or None,
        )
        client.delete_all_memories(user_id)
    except Mem0Error as exc:
        import warnings

        warnings.warn(
            f"Could not clean up memories for {user_id}: {exc}",
            stacklevel=1,
        )
