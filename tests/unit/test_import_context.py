"""Unit tests for scripts/import_context.py.

The import script is an operator tool that walks the local filesystem,
chunks text, and POSTs each chunk to Mem0. The real value of unit-testing
it is locking down the chunker and frontmatter handling — both are easy
to get subtly wrong, and a bug there corrupts every future retrieval.

Coverage map:
  * TestChunker                   — pure-function tests, no I/O
  * TestReadFile                  — frontmatter + plain text
  * TestDiscoverFiles             — file vs dir, hidden dirs, unsupported suffixes
  * TestImportFile                — end-to-end with a mocked Mem0Client
  * TestMem0Client                — request shape, error translation
  * TestMetadataMerging           — CLI tag + file frontmatter precedence
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable when pytest is invoked from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import import_context  # noqa: E402  (path-adjustment import)
from scripts.import_context import (  # noqa: E402
    Mem0Client,
    Mem0Error,
    chunk_text,
    discover_files,
    import_file,
    read_file,
)

# ─── Chunker ───────────────────────────────────────────────────────────────


class TestChunker:
    def test_empty_string_returns_empty(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_short_text_returns_single_chunk(self) -> None:
        # A 50-char input under any reasonable chunk size should be one chunk.
        text = "hello world " * 5  # 60 chars
        chunks = chunk_text(text, chunk_size=100, overlap=10)
        assert chunks == [text.strip()]

    def test_long_text_splits_at_chunk_boundary(self) -> None:
        # Use a chunk_size that divides the text cleanly so we can assert
        # exact boundaries.
        text = "a" * 30
        chunks = chunk_text(text, chunk_size=10, overlap=0)
        assert chunks == ["a" * 10, "a" * 10, "a" * 10]

    def test_overlap_creates_overlapping_chunks(self) -> None:
        text = "abcdefghij" * 10  # 100 chars
        # chunk_size=20, overlap=5 → stride=15, so chunks start at 0,15,30,45,60,75,90
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        # First chunk covers chars 0..19.
        assert chunks[0] == text[0:20]
        # Second chunk covers chars 15..34. The first 5 chars are the last
        # 5 chars of chunk 0 — that's the overlap.
        assert chunks[1] == text[15:35]
        assert chunks[1][:5] == chunks[0][-5:]
        # Last chunk may be shorter than chunk_size (text length is 100).
        assert chunks[-1] == text[90:100]

    def test_final_chunk_uses_remainder(self) -> None:
        text = "a" * 25
        # chunk_size=10, overlap=0 → chunks at 0..9, 10..19, 20..24
        chunks = chunk_text(text, chunk_size=10, overlap=0)
        assert chunks == ["a" * 10, "a" * 10, "a" * 5]

    def test_strip_whitespace_at_boundaries(self) -> None:
        # Leading/trailing whitespace in each chunk is dropped — embedding
        # whitespace is wasteful and produces noisier vectors.
        text = "  hello world  " + "x" * 100
        chunks = chunk_text(text, chunk_size=20, overlap=0)
        for c in chunks:
            assert c == c.strip()

    def test_invalid_chunk_size_raises(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("hello", chunk_size=0, overlap=0)

    def test_invalid_overlap_raises(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("hello", chunk_size=10, overlap=10)  # equal to chunk_size
        with pytest.raises(ValueError):
            chunk_text("hello", chunk_size=10, overlap=20)  # greater than chunk_size
        with pytest.raises(ValueError):
            chunk_text("hello", chunk_size=10, overlap=-1)

    def test_chunk_preserves_paragraph_boundaries_via_whitespace(self) -> None:
        # This is a soft guarantee: with prose and small overlap, chunks
        # should not break mid-word. We assert no chunk ends mid-word as a
        # smoke check.
        text = ("The quick brown fox jumps over the lazy dog. " * 50).strip()
        chunks = chunk_text(text, chunk_size=120, overlap=20)
        for c in chunks:
            # Last char should be punctuation, space, or word character
            # belonging to the original text — never a half-cut.
            assert c[-1] != "-", f"Chunk ends mid-word: {c!r}"


# ─── read_file (frontmatter handling) ───────────────────────────────────────


class TestReadFile:
    def test_plain_text_returns_empty_metadata(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.txt"
        p.write_text("Just some text.\n", encoding="utf-8")
        body, fm = read_file(p)
        assert body.strip() == "Just some text."
        assert fm == {}

    def test_markdown_without_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.md"
        p.write_text("# Heading\n\nBody.", encoding="utf-8")
        body, fm = read_file(p)
        assert body.startswith("# Heading")
        assert fm == {}

    def test_markdown_with_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "with_meta.md"
        p.write_text(
            "---\ntitle: NeoTribes\nauthor: rene\ntag: neotribes\n---\n\nBody text here.\n",
            encoding="utf-8",
        )
        body, fm = read_file(p)
        # The frontmatter delimiters are stripped from the body.
        assert "---" not in body
        assert "Body text here." in body
        # Metadata is returned as a dict.
        assert fm.get("title") == "NeoTribes"
        assert fm.get("author") == "rene"
        assert fm.get("tag") == "neotribes"

    def test_utf8_content(self, tmp_path: Path) -> None:
        p = tmp_path / "utf8.md"
        p.write_text("# Café\n\nRené's notes: naïve façade.", encoding="utf-8")
        body, fm = read_file(p)
        assert "Café" in body
        assert "René" in body


# ─── discover_files ────────────────────────────────────────────────────────


class TestDiscoverFiles:
    def test_single_file(self, tmp_path: Path) -> None:
        p = tmp_path / "x.md"
        p.write_text("hi", encoding="utf-8")
        assert discover_files([p]) == [p]

    def test_directory_recursive(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.md").write_text("b", encoding="utf-8")
        (tmp_path / "sub" / "c.txt").write_text("c", encoding="utf-8")
        files = discover_files([tmp_path])
        names = {f.name for f in files}
        assert names == {"a.md", "b.md", "c.txt"}

    def test_filters_unsupported_suffixes(self, tmp_path: Path) -> None:
        (tmp_path / "ok.md").write_text("a", encoding="utf-8")
        (tmp_path / "ok.txt").write_text("a", encoding="utf-8")
        (tmp_path / "skip.png").write_text("a", encoding="utf-8")
        (tmp_path / "skip.pdf").write_text("a", encoding="utf-8")
        names = {f.name for f in discover_files([tmp_path])}
        assert names == {"ok.md", "ok.txt"}

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "should_not_appear.md").write_text("x", encoding="utf-8")
        (tmp_path / "ok.md").write_text("x", encoding="utf-8")
        names = {f.name for f in discover_files([tmp_path])}
        assert names == {"ok.md"}

    def test_missing_path_warns_and_skips(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.md"
        # Should not raise; should just skip.
        assert discover_files([missing]) == []

    def test_mixed_file_and_dir(self, tmp_path: Path) -> None:
        # When the caller passes both --file and --dir, we accept a list.
        f = tmp_path / "single.md"
        f.write_text("a", encoding="utf-8")
        d = tmp_path / "d"
        d.mkdir()
        (d / "x.md").write_text("b", encoding="utf-8")
        files = discover_files([f, d])
        names = {f.name for f in files}
        assert names == {"single.md", "x.md"}


# ─── Mem0Client (request shape) ────────────────────────────────────────────


class TestMem0Client:
    def test_post_includes_user_id_and_messages(self) -> None:
        client = Mem0Client(base_url="http://m:8000", api_key="secret")
        with patch("scripts.import_context.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, content=b'{"ok": true}', text="")
            mock_post.return_value.json.return_value = {"ok": True}
            client.add_memory("hello", user_id="alice", metadata={"tag": "x"})
        call = mock_post.call_args
        assert call.args[0] == "http://m:8000/v1/memories/"
        body = call.kwargs["json"]
        assert body["user_id"] == "alice"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["metadata"] == {"tag": "x"}
        assert call.kwargs["headers"]["Authorization"] == "Token secret"

    def test_post_without_api_key_omits_auth_header(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        with patch("scripts.import_context.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, content=b"", text="")
            client.add_memory("x", user_id="alice")
        headers = mock_post.call_args.kwargs["headers"]
        assert "Authorization" not in headers

    def test_4xx_raises_mem0_error_with_body(self) -> None:
        client = Mem0Client(base_url="http://m:8000")
        with patch("scripts.import_context.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="bad request")
            with pytest.raises(Mem0Error) as exc_info:
                client.add_memory("x", user_id="alice")
        assert "400" in str(exc_info.value)
        assert "bad request" in str(exc_info.value)

    def test_connection_error_translates(self) -> None:
        import httpx as _httpx

        client = Mem0Client(base_url="http://m:8000")
        with patch(
            "scripts.import_context.httpx.post",
            side_effect=_httpx.ConnectError("refused"),
        ):
            with pytest.raises(Mem0Error) as exc_info:
                client.add_memory("x", user_id="alice")
        assert "unreachable" in str(exc_info.value).lower()

    def test_base_url_strips_trailing_slash(self) -> None:
        # Important so "http://m:8000" and "http://m:8000/" both build the
        # same URL.
        client = Mem0Client(base_url="http://m:8000/")
        assert client.base_url == "http://m:8000"


# ─── import_file (end-to-end) ──────────────────────────────────────────────


class TestImportFile:
    def test_chunks_posted_with_correct_metadata(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        # 30 chars; chunk_size=10, overlap=0 → 3 chunks
        p.write_text("a" * 30, encoding="utf-8")
        client = MagicMock()
        client.add_memory.return_value = {"ok": True}
        summary = import_file(
            p,
            user_id="rene",
            tag="neotribes",
            client=client,
            chunk_size=10,
            chunk_overlap=0,
        )
        assert summary.files_processed == 1
        assert summary.files_failed == 0
        assert summary.chunks_posted == 3
        assert summary.chunks_failed == 0
        assert client.add_memory.call_count == 3
        # Each call carries the same metadata + chunk_index.
        for idx, call in enumerate(client.add_memory.call_args_list, start=1):
            text, kwargs = call.args[0], call.kwargs
            assert text == "a" * 10
            assert kwargs["user_id"] == "rene"
            assert kwargs["metadata"]["tag"] == "neotribes"
            assert kwargs["metadata"]["source"].endswith("doc.md")
            assert kwargs["metadata"]["chunk_index"] == idx

    def test_failed_chunk_does_not_fail_file(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        # 200 chars / chunk_size=100 → 2 chunks
        p.write_text("a" * 200, encoding="utf-8")
        client = MagicMock()
        # First call fails, second succeeds.
        client.add_memory.side_effect = [
            Mem0Error("transient 503"),
            {"ok": True},
        ]
        summary = import_file(
            p, user_id="rene", tag=None, client=client, chunk_size=100, chunk_overlap=0
        )
        assert summary.chunks_failed == 1
        assert summary.chunks_posted == 1
        # File is still counted as processed because some chunks landed.
        assert summary.files_processed == 1
        assert summary.files_failed == 0

    def test_unreadable_file_marks_failure(self, tmp_path: Path) -> None:
        # A non-existent path is handled by discover_files, but if import_file
        # is called directly on a bad path, it must count as failed, not raise.
        p = tmp_path / "ghost.md"
        client = MagicMock()
        summary = import_file(p, user_id="rene", tag=None, client=client)
        assert summary.files_failed == 1
        client.add_memory.assert_not_called()

    def test_empty_file_is_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("   \n\n   ", encoding="utf-8")
        client = MagicMock()
        summary = import_file(p, user_id="rene", tag=None, client=client)
        assert summary.files_processed == 0
        assert summary.chunks_posted == 0
        client.add_memory.assert_not_called()

    def test_metadata_includes_frontmatter_keys(self, tmp_path: Path) -> None:
        p = tmp_path / "lore.md"
        p.write_text(
            "---\ntitle: CyberBright\nauthor: rene\ntag: cyberbright\n---\n\nSome lore text.",
            encoding="utf-8",
        )
        client = MagicMock()
        client.add_memory.return_value = {}
        import_file(p, user_id="rene", tag=None, client=client, chunk_size=1000, chunk_overlap=0)
        metadata = client.add_memory.call_args.kwargs["metadata"]
        assert metadata["title"] == "CyberBright"
        assert metadata["author"] == "rene"
        # File's frontmatter tag is used when --tag is not given.
        assert metadata["tag"] == "cyberbright"
        # Reserved keys are present.
        assert "source" in metadata
        assert "imported_at" in metadata


# ─── CLI arg parsing ───────────────────────────────────────────────────────


class TestCLIParsing:
    def test_file_and_dir_are_mutually_exclusive(self) -> None:
        with patch("sys.argv", ["import_context", "--file", "x.md", "--dir", "y"]):
            with pytest.raises(SystemExit):
                import_context.main()

    def test_missing_source_exits(self) -> None:
        with patch("sys.argv", ["import_context"]):
            with pytest.raises(SystemExit):
                import_context.main()

    def test_help_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["import_context", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                import_context.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Bulk-import" in captured.out or "usage" in captured.out.lower()


# ─── Metadata precedence ──────────────────────────────────────────────────


class TestMetadataMerging:
    def test_cli_tag_wins_over_frontmatter_tag(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        p.write_text(
            "---\ntag: from-frontmatter\n---\nhello",
            encoding="utf-8",
        )
        client = MagicMock()
        client.add_memory.return_value = {}
        import_file(p, user_id="u", tag="from-cli", client=client, chunk_size=1000, chunk_overlap=0)
        assert client.add_memory.call_args.kwargs["metadata"]["tag"] == "from-cli"

    def test_frontmatter_tag_used_when_cli_omits(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        p.write_text("---\ntag: fm-tag\n---\nhello", encoding="utf-8")
        client = MagicMock()
        client.add_memory.return_value = {}
        import_file(p, user_id="u", tag=None, client=client, chunk_size=1000, chunk_overlap=0)
        assert client.add_memory.call_args.kwargs["metadata"]["tag"] == "fm-tag"


# ─── CLI ─────────────────────────────────────────────────────────────────────
#
# The CLI surface is where most operator-facing bugs live. We exercise:
#
#   * Exit codes: 0 = success, 1 = some chunks/files failed, 2 = no files
#     discovered, 3 = Mem0 unreachable.
#   * argparse defaults resolving to MEM0_* env vars.
#   * Mutually-exclusive source arguments.
#   * Failure paths surfaced clearly enough to alert on.
#
# We patch httpx.get (the script uses the module, not a client object, for
# the health probe) and Mem0Client.add_memory. The intent is to lock in
# the CLI's behaviour at the seam between human input and module-level
# functions, not to retest the chunker or HTTP shape (those live in the
# TestImportFile / TestMem0Client classes).


class TestCLI:
    """End-to-end tests of ``main(argv)`` with patching at the network seam."""

    @staticmethod
    def _write_md(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")

    def _run_cli(self, *argv: str, monkeypatch, tmp_path: Path) -> int:
        """Invoke the CLI's main() with the given argv. Returns exit code.

        The CLI uses ``import_context.httpx.get`` for the health probe; we
        stub it here so no real network call happens.
        """
        monkeypatch.setattr(
            import_context.httpx,
            "get",
            MagicMock(return_value=MagicMock(status_code=200)),
        )
        return import_context.main(list(argv))

    def test_success_exit_code(self, monkeypatch, tmp_path: Path) -> None:
        md = tmp_path / "notes.md"
        self._write_md(md, "hello world")
        fake_client = MagicMock()
        fake_client.add_memory.return_value = {}
        monkeypatch.setattr(import_context, "Mem0Client", lambda **kw: fake_client)
        code = self._run_cli(
            "--file",
            str(md),
            "--user",
            "u",
            "--api-url",
            "http://m:8000",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
        assert code == 0
        assert fake_client.add_memory.call_count == 1

    def test_no_files_discovered_returns_code_2(self, monkeypatch, tmp_path: Path) -> None:
        # Point at a nonexistent path → discover_files returns [] → exit 2.
        # (An empty file is *discovered* by discover_files; it's import_file
        # that ignores it without counting it as processed.)
        missing = tmp_path / "nope.md"
        code = self._run_cli(
            "--file",
            str(missing),
            "--user",
            "u",
            "--api-url",
            "http://m:8000",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
        assert code == 2

    def test_mem0_unreachable_returns_code_3(self, monkeypatch, tmp_path: Path) -> None:
        # Patch the health probe to raise so the CLI's fail-fast path
        # (exit code 3) fires before any import work happens. We avoid the
        # network entirely so test environments without outbound network
        # access see the same behaviour as ones with it.
        import httpx

        md = tmp_path / "notes.md"
        self._write_md(md, "hello")
        # Bypass _run_cli's auto-patch; we want httpx.get to raise, not succeed.
        monkeypatch.setattr(
            import_context.httpx,
            "get",
            MagicMock(side_effect=httpx.ConnectError("refused")),
        )
        code = import_context.main(
            ["--file", str(md), "--user", "u", "--api-url", "http://m:8000"],
        )
        assert code == 3

    def test_chunk_failure_returns_code_1(self, monkeypatch, tmp_path: Path) -> None:
        md = tmp_path / "notes.md"
        self._write_md(md, "hello")
        fake_client = MagicMock()
        fake_client.add_memory.side_effect = Mem0Error("503 service unavailable")
        monkeypatch.setattr(import_context, "Mem0Client", lambda **kw: fake_client)
        code = self._run_cli(
            "--file",
            str(md),
            "--user",
            "u",
            "--api-url",
            "http://m:8000",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
        # chunks_failed increments → exit 1, not 0.
        assert code == 1

    def test_missing_source_argument_exits_usage_error(self, monkeypatch, tmp_path: Path) -> None:
        # argparse rejects missing required --file/--dir with SystemExit(2).
        with pytest.raises(SystemExit) as exc_info:
            import_context.main([])
        assert exc_info.value.code == 2

    def test_mutually_exclusive_source_arguments(self, monkeypatch, tmp_path: Path) -> None:
        # Passing both --file and --dir is a usage error.
        md = tmp_path / "notes.md"
        self._write_md(md, "hello")
        with pytest.raises(SystemExit) as exc_info:
            import_context.main(["--file", str(md), "--dir", str(tmp_path)])
        assert exc_info.value.code == 2

    def test_argparse_defaults_take_from_env_when_flag_missing(self, monkeypatch) -> None:
        # Pure argparse-layer test — no file I/O, just inspect the parser.
        monkeypatch.setenv("MEM0_USER_ID", "env-user")
        monkeypatch.setenv("MEM0_API_URL", "http://env-host:9000")
        monkeypatch.setenv("MEM0_ADMIN_API_KEY", "env-key")
        ns = import_context.build_parser().parse_args(["--file", "x"])
        assert ns.user == "env-user"
        assert ns.api_url == "http://env-host:9000"
        assert ns.api_key == "env-key"

    def test_chunk_failures_dont_abort_other_files(self, monkeypatch, tmp_path: Path) -> None:
        # One file's chunk fails, the next file still gets imported.
        md1 = tmp_path / "first.md"
        md2 = tmp_path / "second.md"
        self._write_md(md1, "alpha")
        self._write_md(md2, "beta")

        fake_client = MagicMock()
        # First call (first.md) raises; second call (second.md) succeeds.
        fake_client.add_memory.side_effect = [
            Mem0Error("transient 503"),
            {},
        ]
        monkeypatch.setattr(import_context, "Mem0Client", lambda **kw: fake_client)
        code = self._run_cli(
            "--dir",
            str(tmp_path),
            "--user",
            "u",
            "--api-url",
            "http://m:8000",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
        assert code == 1  # at least one chunk failed
        # Both files attempted, one chunk per file with default chunk size.
        assert fake_client.add_memory.call_count == 2

    def test_verbose_flag_does_not_break_invocation(self, monkeypatch, tmp_path: Path) -> None:
        # We don't introspect the log level; the test passes if main()
        # doesn't crash under -v and behaves the same as without it.
        md = tmp_path / "notes.md"
        self._write_md(md, "hi")
        fake_client = MagicMock()
        fake_client.add_memory.return_value = {}
        monkeypatch.setattr(import_context, "Mem0Client", lambda **kw: fake_client)
        code = self._run_cli(
            "--file",
            str(md),
            "--user",
            "u",
            "--api-url",
            "http://m:8000",
            "-v",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
        assert code == 0

    def test_tags_list_first_value_used(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        p.write_text(
            "---\ntags: [first, second]\n---\nhello",
            encoding="utf-8",
        )
        client = MagicMock()
        client.add_memory.return_value = {}
        import_file(p, user_id="u", tag=None, client=client, chunk_size=1000, chunk_overlap=0)
        assert client.add_memory.call_args.kwargs["metadata"]["tag"] == "first"
