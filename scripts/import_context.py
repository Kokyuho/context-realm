#!/usr/bin/env python3
"""
ContextRealm — bulk import documents and notes into Mem0.

Path B of Phase 6: persistent project knowledge lives in Mem0 and survives
across sessions. This script reads local files (markdown with optional YAML
frontmatter, plus plain text) and POSTs each chunk to Mem0's /v1/memories/
endpoint, tagged with source file and user-supplied tag for later filtering.

Usage:
  python scripts/import_context.py --file my_projects.md --user rene --tag neotribes
  python scripts/import_context.py --dir ./notes/ --user rene
  python scripts/import_context.py --file lore.md --user rene --api-url http://localhost:8000

Environment variables (overridden by flags):
  MEM0_API_URL       default http://localhost:8000
  MEM0_ADMIN_API_KEY used as Authorization: Token <key> when set
  MEM0_USER_ID       default 'default'

Idempotency: Mem0's fact-extraction LLM deduplicates similar memories on
ingest, so re-running this script against the same files is safe — duplicate
chunks collapse into a single stored memory.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import httpx

logger = logging.getLogger("contextrealm.import")

# ─── Defaults ──────────────────────────────────────────────────────────────
DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_USER_ID = "default"
DEFAULT_CHUNK_SIZE = 1500  # characters; ~300-400 tokens of prose
DEFAULT_CHUNK_OVERLAP = 200  # characters; small enough to keep chunks distinct
DEFAULT_BATCH_PAUSE_S = 0.05  # polite pause between Mem0 calls
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc"}

# Frontmatter is delimited by `---` lines. We strip it before chunking so the
# metadata doesn't leak into the embedded text. (frontmatter.load already
# separates them, but we want the raw text for chunking.)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


# ─── Data types ────────────────────────────────────────────────────────────


@dataclass
class ImportSummary:
    files_processed: int = 0
    files_failed: int = 0
    chunks_posted: int = 0
    chunks_failed: int = 0

    def merge(self, other: ImportSummary) -> None:
        self.files_processed += other.files_processed
        self.files_failed += other.files_failed
        self.chunks_posted += other.chunks_posted
        self.chunks_failed += other.chunks_failed


# ─── Mem0 REST client (mirrors tests/helpers/mem0_client.py) ───────────────


class Mem0Error(RuntimeError):
    """Raised when Mem0 returns a non-2xx or is unreachable."""


class Mem0Client:
    """Thin sync client. Keeps the script's dependency surface minimal."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"
        return headers

    def add_memory(
        self,
        text: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """POST /v1/memories/ — send a single chunk for fact extraction + storage.

        Mem0 takes an OpenAI-style messages array; the simplest valid input
        is ``[{"role": "user", "content": text}]`` which the fact-extraction
        LLM treats as the user telling it a fact.
        """
        url = f"{self.base_url}/v1/memories/"
        body: dict = {
            "messages": [{"role": "user", "content": text}],
            "user_id": user_id,
        }
        if metadata:
            body["metadata"] = metadata
        try:
            r = httpx.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        except httpx.RequestError as exc:
            raise Mem0Error(f"Mem0 unreachable at {url}: {exc}") from exc
        if r.status_code >= 400:
            raise Mem0Error(f"Mem0 POST /v1/memories/ -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}


# ─── Pure helpers (no I/O, easy to test) ───────────────────────────────────


def chunk_text(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping character-window chunks.

    We split on character count rather than tokens because the script is
    intentionally dep-light (no tokenizer). A 1500-char chunk of English
    prose is ~300-400 tokens, well under every frontier model's context
    limit and comfortably above Mem0's useful-fact floor.

    Chunks are produced by sliding a window of ``chunk_size`` characters,
    advancing by ``chunk_size - overlap`` after each cut. The overlap
    ensures facts near a chunk boundary are present in two adjacent chunks
    — useful for embeddings where a single fact might be split.
    """
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError(
            f"overlap must be in [0, chunk_size); got overlap={overlap}, chunk_size={chunk_size}"
        )

    stride = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        start += stride
    return chunks


def read_file(path: Path) -> tuple[str, dict]:
    """Return (body_without_frontmatter, metadata_dict).

    Supports Markdown with YAML frontmatter (parsed via python-frontmatter).
    For files without frontmatter, ``python-frontmatter`` still returns the
    raw content with an empty metadata dict, so we don't need to special-case
    plain text.
    """
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    return post.content, dict(post.metadata or {})


def discover_files(paths: Iterable[Path]) -> list[Path]:
    """Expand a mix of file and directory paths into a sorted file list.

    Symlinks are followed. Hidden directories (starting with '.') are
    skipped to avoid walking into .git, .venv, etc. Non-text files are
    silently filtered by extension.
    """
    out: list[Path] = []
    for p in paths:
        if not p.exists():
            logger.warning("Skipping missing path: %s", p)
            continue
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_SUFFIXES:
                out.append(p)
            else:
                logger.debug("Skipping unsupported file type: %s", p)
            continue
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if not child.is_file():
                    continue
                if any(part.startswith(".") for part in child.relative_to(p).parts):
                    continue
                if child.suffix.lower() in SUPPORTED_SUFFIXES:
                    out.append(child)
    # De-duplicate while preserving sort order.
    return sorted(set(out))


# ─── Import orchestration ─────────────────────────────────────────────────


def import_file(
    path: Path,
    user_id: str,
    tag: str | None,
    client: Mem0Client,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> ImportSummary:
    """Chunk one file and POST each chunk to Mem0. Never raises — counts failures."""
    summary = ImportSummary()
    try:
        body, fm = read_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("Failed to read %s: %s", path, exc)
        summary.files_failed += 1
        return summary

    if not body.strip():
        logger.info("Skipping empty file: %s", path)
        return summary

    chunks = chunk_text(body, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        logger.info("No chunks produced for %s", path)
        return summary

    # Merge: CLI --tag < file frontmatter 'tag' < file frontmatter 'tags' list
    metadata: dict = {
        "source": str(path),
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if tag:
        metadata["tag"] = tag
    file_tag = fm.get("tag") or (
        fm.get("tags", [None])[0] if isinstance(fm.get("tags"), list) else None
    )
    if file_tag and "tag" not in metadata:
        metadata["tag"] = file_tag
    for key, value in fm.items():
        # Reserve our own keys; surface everything else (title, author, etc.)
        if key not in {"tag", "tags", "source", "imported_at"}:
            metadata[key] = value

    for idx, chunk in enumerate(chunks, start=1):
        try:
            client.add_memory(chunk, user_id=user_id, metadata={**metadata, "chunk_index": idx})
            summary.chunks_posted += 1
        except Mem0Error as exc:
            logger.warning("Failed to import chunk %d of %s: %s", idx, path, exc)
            summary.chunks_failed += 1
        # Polite pause to keep the host calm during big imports.
        if DEFAULT_BATCH_PAUSE_S:
            time.sleep(DEFAULT_BATCH_PAUSE_S)

    summary.files_processed += 1
    logger.info("Imported %d chunks from %s", len(chunks), path)
    return summary


# ─── CLI ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="import_context",
        description="Bulk-import documents and notes into ContextRealm (Mem0).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="Path to a single file to import.")
    src.add_argument(
        "--dir", dest="directory", type=Path, help="Path to a directory to walk recursively."
    )

    p.add_argument(
        "--user",
        default=os.environ.get("MEM0_USER_ID", DEFAULT_USER_ID),
        help="Mem0 user_id to attribute the memories to.",
    )
    p.add_argument(
        "--tag",
        default=None,
        help="Optional tag stored in each memory's metadata (e.g. 'neotribes').",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("MEM0_API_URL", DEFAULT_API_URL),
        help="Mem0 server base URL.",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("MEM0_ADMIN_API_KEY") or None,
        help="Mem0 admin API key (Authorization: Token). Auto-read from MEM0_ADMIN_API_KEY.",
    )
    p.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Approximate chunk size in characters.",
    )
    p.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Overlap between consecutive chunks in characters.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    targets = [args.file] if args.file else [args.directory]
    files = discover_files(targets)
    if not files:
        logger.error("No importable files found under: %s", targets)
        return 2

    client = Mem0Client(base_url=args.api_url, api_key=args.api_key)
    # Fail fast if Mem0 is unreachable — don't half-import and pretend success.
    try:
        httpx.get(f"{args.api_url.rstrip('/')}/health", timeout=5.0)
    except httpx.RequestError as exc:
        logger.error("Mem0 is unreachable at %s: %s", args.api_url, exc)
        return 3

    logger.info("Importing %d file(s) as user_id=%r into %s", len(files), args.user, args.api_url)
    total = ImportSummary()
    for path in files:
        total.merge(
            import_file(
                path,
                user_id=args.user,
                tag=args.tag,
                client=client,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
            )
        )

    print(
        f"Done. files_ok={total.files_processed} files_failed={total.files_failed} "
        f"chunks_ok={total.chunks_posted} chunks_failed={total.chunks_failed}"
    )
    return 0 if total.files_failed == 0 and total.chunks_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
