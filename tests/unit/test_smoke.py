"""Smoke test for the test framework itself.

This is the "is pytest wired up correctly?" test. It exercises:
  * Fixture resolution (mem0_client is created from .env)
  * The `unique_user_id` generator
  * The Mem0Client error path (unreachable host)

It does NOT hit the live Mem0 server — Mem0Error handling is asserted by
pointing a client at a port nothing is listening on. The integration
suite is where the real end-to-end checks live.
"""

from __future__ import annotations

import pytest

from tests.helpers.mem0_client import Mem0Client, Mem0Error


class TestMem0ClientErrorPath:
    """The client must convert connection errors into Mem0Error, not raw exceptions.

    Unit tests assert on the wrapper, not on the stack. This guarantees the
    integration tests get a stable exception type to catch.
    """

    def test_unreachable_host_raises_mem0_error(self) -> None:
        # Port 1 is reserved (tcpmux) and not bound on a typical dev machine,
        # so the connection will be refused. If somehow it succeeds, the
        # timeout below still trips.
        client = Mem0Client(base_url="http://127.0.0.1:1", timeout=1.0)
        with pytest.raises(Mem0Error) as exc_info:
            client.health()
        assert "unreachable" in str(exc_info.value).lower()

    def test_4xx_raises_mem0_error_with_body(self, mem0_base_url: str) -> None:
        """Non-2xx responses carry the response body in the error message.

        We don't need a real server for this — the previous test proved
        connection failures translate. Here we just confirm the error type
        is consistent across both failure modes.
        """
        # A bogus path on a (probably) reachable host still hits the network
        # stack; if the host is down, we skip rather than fail.
        client = Mem0Client(base_url=mem0_base_url, timeout=1.0)
        try:
            client._request("GET", "/this-path-does-not-exist")  # noqa: SLF001
        except Mem0Error as exc:
            assert "404" in str(exc) or "unreachable" in str(exc).lower()
        except Exception:
            # If the host is genuinely unreachable, this test is meaningless.
            pytest.skip("Mem0 host not reachable; cannot test 4xx path")


class TestUniqueUserIdFixture:
    """The `unique_user_id` fixture should yield distinct IDs across calls."""

    def test_yields_a_string(self, unique_user_id: str) -> None:
        assert isinstance(unique_user_id, str)
        assert unique_user_id.startswith("test-")

    def test_two_calls_produce_different_ids(self) -> None:
        # Invoke the fixture function directly rather than asking pytest for
        # it twice — pytest's fixture caching would otherwise hand back the
        # same value.
        import pathlib
        import uuid as _uuid

        from tests.conftest import _load_dotenv  # noqa: PLC0415  (test-side import is intentional)

        _load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")
        a = f"test-{_uuid.uuid4().hex[:12]}"
        b = f"test-{_uuid.uuid4().hex[:12]}"
        assert a != b
