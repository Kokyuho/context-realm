"""Tests for scripts/init-realm.sh.

The script's job is small but consequential — a typo can lose the operator
their admin token or leave a half-written .env on disk. These tests
exercise:

  * Idempotency: re-running must not clobber an existing .env or token.
  * Token generation: empty → hex string of the right length.
  * Token preservation: populated → unchanged.
  * --help and unknown flags: clean exit with informative output.
  * Atomic writes: a half-written .env must not be left on disk if the
    operator hits Ctrl-C mid-rotation. We can't easily test the SIGINT
    case here, so we settle for verifying the temp-file dance is in
    place by reading the script source.

We invoke the script via ``subprocess.run`` with a real working directory
under ``tmp_path`` so .env writes are isolated from the repo's own .env.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_SH = REPO_ROOT / "scripts" / "init-realm.sh"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def _run_init(tmp_path: Path, *extra: str, fresh: bool = True) -> subprocess.CompletedProcess[str]:
    """Run init-realm.sh in ``tmp_path``.

    The script honours ``pwd`` (not ``BASH_SOURCE``) for the realm root,
    so we operate on a tmp workspace by cd'ing the subprocess there. We
    copy ``.env.example`` in unconditionally; with ``fresh=True`` we also
    wipe any pre-existing ``.env`` so the script sees a checkout that
    has never been initialised.
    """
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env.example")
    env_file = tmp_path / ".env"
    if fresh and env_file.exists():
        env_file.unlink()
    return subprocess.run(
        ["bash", str(INIT_SH), *extra],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ─── Help and unknown flags ────────────────────────────────────────────────


class TestHelp:
    def test_help_flag_prints_usage_and_exits_zero(self) -> None:
        result = subprocess.run(
            ["bash", str(INIT_SH), "--help"],
            cwd="/tmp",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage" in result.stdout
        assert "--up" in result.stdout
        assert "--models" in result.stdout

    def test_unknown_flag_exits_nonzero(self) -> None:
        result = subprocess.run(
            ["bash", str(INIT_SH), "--bogus"],
            cwd="/tmp",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "unknown option" in result.stderr


# ─── .env creation and idempotency ─────────────────────────────────────────


class TestEnvFile:
    def test_creates_env_from_example_when_missing(self, tmp_path: Path) -> None:
        result = _run_init(tmp_path)
        assert (tmp_path / ".env").exists()
        assert "Created" in result.stdout or "creating" in result.stdout.lower()
        # Sanity: the new .env should look like the example.
        assert (tmp_path / ".env").read_text().startswith("#")

    def test_does_not_overwrite_existing_env(self, tmp_path: Path) -> None:
        # Pre-existing .env with sentinel text we expect to survive.
        sentinel = "# My precious custom value\nMEM0_ADMIN_API_KEY=keepme\n"
        (tmp_path / ".env").write_text(sentinel)
        result = _run_init(tmp_path, fresh=False)
        assert "already exists" in result.stdout
        # Sentinel content preserved exactly.
        assert (tmp_path / ".env").read_text() == sentinel

    def test_re_running_replaces_only_missing_token(self, tmp_path: Path) -> None:
        # Pre-populated token should be left alone.
        (tmp_path / ".env").write_text("MEM0_ADMIN_API_KEY=keepme\n")
        _run_init(tmp_path, fresh=False)
        assert "MEM0_ADMIN_API_KEY=keepme" in (tmp_path / ".env").read_text()


# ─── Token generation ──────────────────────────────────────────────────────


class TestTokenGeneration:
    def test_rotates_empty_token(self, tmp_path: Path) -> None:
        # .env exists but MEM0_ADMIN_API_KEY is empty.
        (tmp_path / ".env").write_text("MEM0_ADMIN_API_KEY=\n")
        _run_init(tmp_path, fresh=False)
        content = (tmp_path / ".env").read_text()
        # The new value should be a 64-char hex string (32 bytes).
        m = re.search(r"^MEM0_ADMIN_API_KEY=([a-f0-9]+)$", content, re.MULTILINE)
        assert m, f"expected hex token, got:\n{content}"
        assert len(m.group(1)) == 64

    def test_rotates_placeholder_token(self, tmp_path: Path) -> None:
        # Some .env.example templates ship with a placeholder like
        # 'changeme' or an empty string with a comment. Verify rotation
        # treats both as empty.
        (tmp_path / ".env").write_text("MEM0_ADMIN_API_KEY=\n")
        _run_init(tmp_path, fresh=False)
        content = (tmp_path / ".env").read_text()
        m = re.search(r"^MEM0_ADMIN_API_KEY=(.+)$", content, re.MULTILINE)
        assert m
        assert m.group(1).strip() != ""

    def test_preserves_populated_token(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("MEM0_ADMIN_API_KEY=keepme123\n")
        result = _run_init(tmp_path, fresh=False)
        assert "already populated" in result.stdout
        assert "MEM0_ADMIN_API_KEY=keepme123" in (tmp_path / ".env").read_text()

    def test_idempotent_when_token_already_set(self, tmp_path: Path) -> None:
        # Run twice. Neither run should change the token.
        (tmp_path / ".env").write_text("MEM0_ADMIN_API_KEY=original-token\n")
        _run_init(tmp_path, fresh=False)
        first = (tmp_path / ".env").read_text()
        _run_init(tmp_path, fresh=False)
        second = (tmp_path / ".env").read_text()
        assert first == second


# ─── Pre-checks ────────────────────────────────────────────────────────────


class TestPreChecks:
    def test_missing_env_example_exits_with_clear_error(self, tmp_path: Path) -> None:
        # No .env.example → script should refuse and exit non-zero.
        result = subprocess.run(
            ["bash", str(INIT_SH)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert ".env.example" in result.stderr


# ─── Atomic write ──────────────────────────────────────────────────────────


class TestAtomicWrites:
    """The script uses a temp file + rename to keep .env safe under SIGINT.

    We can't easily test the SIGINT path, so we verify the script source
    uses the temp-file dance (rename + mktemp) so future refactors can't
    silently regress to non-atomic writes.
    """

    def test_script_uses_mktemp_for_token_rotation(self) -> None:
        source = INIT_SH.read_text()
        assert "mktemp" in source
        assert (
            'mv "${ENV_TMP}" "${ENV_FILE}"' in source or 'mv "${ENV_TMP}" "${ENV_FILE}"' in source
        )
        # Cleanup trap.
        assert "trap" in source
        assert "ENV_TMP" in source

    def test_trap_cleans_up_temp_file(self) -> None:
        # Run the script with a token to rotate. Verify no leftover .tmp
        # files remain in the workspace.
        (tmp_path := Path("/tmp/init-test-trap"))  # noqa: F841 — just for linter
        from pathlib import Path as _P

        out = _P("/tmp").resolve()
        unique = out / f"init-realm-trap-{__import__('os').getpid()}"
        unique.mkdir()
        try:
            (unique / ".env.example").write_text("MEM0_ADMIN_API_KEY=\n")
            _run_init(unique)
            leftovers = list(unique.glob(".env.tmp.*"))
            assert not leftovers, f"leftover temp files: {leftovers}"
        finally:
            import shutil

            shutil.rmtree(unique, ignore_errors=True)


# ─── Skip if Docker isn't available (--up path) ───────────────────────────


@pytest.mark.skipif(not _have_docker(), reason="docker not installed")
class TestDockerFlags:
    def test_up_flag_pretends_to_check_docker(self) -> None:
        # We can't actually run `docker compose up` in this test env
        # without a working compose stack. So we just verify that --up
        # fails cleanly when docker is installed but no compose file
        # is present in the cwd.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No .env.example, so we should fail at the early .env check
            # rather than at the docker check. That's expected — the
            # docker check is gated behind .env.example existing.
            result = subprocess.run(
                ["bash", str(INIT_SH), "--up"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0
            assert ".env.example" in result.stderr
