"""Tests for scripts/caddy-entrypoint.sh.

The entrypoint writes a runtime Caddyfile based on whether REALM_DOMAIN
is set. The tests verify the two cases produce the expected layout and
that the generated file is structurally sound (has at least one site
address, balanced braces, no obvious garbage).

These tests run the actual script via subprocess in a temporary
directory. We rewrite the hard-coded ``OUT=/etc/caddy/Caddyfile.runtime``
in the script via ``sed`` and run the modified script with bash — bash
will fail on the final ``exec caddy`` (no caddy binary in the test env),
but the Caddyfile is on disk by then, which is what we want to inspect.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "caddy-entrypoint.sh"


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    """Where the entrypoint will write the Caddyfile for this test."""
    return tmp_path / "Caddyfile.runtime"


def _run(env_overrides: dict[str, str], out_path: Path) -> tuple[str, str]:
    """Run the entrypoint with rewritten OUT path. Returns (stdout, stderr).

    The script's final line (``exec caddy ...``) will fail because there
    is no caddy binary on the host; we let that happen non-zero and
    inspect the file it already wrote.
    """
    rewritten = out_path.parent / "entrypoint.sh"
    # Rewrite the hard-coded OUT path. Only one occurrence in the source.
    script_text = ENTRYPOINT.read_text()
    rewritten.write_text(
        script_text.replace(
            "OUT=/etc/caddy/Caddyfile.runtime",
            f"OUT={out_path}",
        )
    )
    rewritten.chmod(0o755)

    env = {**os.environ, **env_overrides}
    result = subprocess.run(
        ["/bin/sh", str(rewritten)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout, result.stderr


def _has_site_address(text: str) -> bool:
    """Loose check: at least one non-comment, non-empty line ending in ``{``."""
    return any(
        s.endswith("{")
        for raw in text.splitlines()
        for s in [raw.strip()]
        if s and not s.startswith("#")
    )


# ─── Production mode (REALM_DOMAIN set) ──────────────────────────────────────


class TestProductionMode:
    def test_includes_production_block(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": "mcp.example.com"}, tmp_out)
        content = tmp_out.read_text()
        assert "mcp.example.com" in content
        assert "reverse_proxy mcp:8765" in content

    def test_also_includes_local_block(self, tmp_out: Path) -> None:
        # Local block is always generated and bound to loopback, so debug
        # curl tests work even in production. Verifies the assumption.
        _run({"REALM_DOMAIN": "mcp.example.com"}, tmp_out)
        content = tmp_out.read_text()
        assert ":8443" in content
        assert "127.0.0.1" in content
        assert "tls internal" in content

    def test_production_block_has_a_site_address(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": "mcp.example.com"}, tmp_out)
        content = tmp_out.read_text()
        assert _has_site_address(content), "expected a `hostname {` line"

    def test_brace_count_is_balanced(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": "mcp.example.com"}, tmp_out)
        content = tmp_out.read_text()
        assert content.count("{") == content.count("}")


# ─── Local-only mode (REALM_DOMAIN empty) ────────────────────────────────────


class TestLocalOnlyMode:
    def test_omits_production_block(self, tmp_out: Path) -> None:
        # No `mcp.example.com` should remain; the entrypoint's local mode
        # skips the production block entirely.
        _run({"REALM_DOMAIN": ""}, tmp_out)
        content = tmp_out.read_text()
        assert "mcp.example.com" not in content

    def test_includes_local_block(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": ""}, tmp_out)
        content = tmp_out.read_text()
        assert ":8443" in content
        assert "127.0.0.1" in content
        assert "tls internal" in content
        assert "reverse_proxy mcp:8765" in content

    def test_local_block_bound_to_loopback(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": ""}, tmp_out)
        assert "127.0.0.1" in tmp_out.read_text()

    def test_still_has_at_least_one_site_address(self, tmp_out: Path) -> None:
        # An empty Caddyfile (no site blocks) is a parse error in Caddy.
        # Even in local-only mode the local block must remain.
        _run({"REALM_DOMAIN": ""}, tmp_out)
        assert _has_site_address(tmp_out.read_text())

    def test_brace_count_is_balanced(self, tmp_out: Path) -> None:
        _run({"REALM_DOMAIN": ""}, tmp_out)
        content = tmp_out.read_text()
        assert content.count("{") == content.count("}")


# ─── Misc ────────────────────────────────────────────────────────────────────


class TestEntrypointItself:
    @pytest.mark.skipif(not ENTRYPOINT.exists(), reason="caddy-entrypoint.sh missing")
    def test_entrypoint_is_executable(self) -> None:
        import stat

        mode = ENTRYPOINT.stat().st_mode
        assert mode & stat.S_IXUSR, "entrypoint must be executable (chmod +x)"

    @pytest.mark.skipif(not ENTRYPOINT.exists(), reason="caddy-entrypoint.sh missing")
    def test_entrypoint_uses_set_eu(self) -> None:
        # We rely on `set -eu` so the script aborts loudly on bad config
        # instead of silently rendering a broken Caddyfile.
        head = "\n".join(ENTRYPOINT.read_text().splitlines()[:25])
        assert "set -eu" in head
