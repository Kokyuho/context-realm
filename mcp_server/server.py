"""ContextRealm — MCP server entry point.

Exposes two tools to any MCP-compatible client:

  * ``search_memories(query, limit=5)`` — semantic search over the Realm's
    Mem0 store. Returns the matching memory text.

  * ``add_memory(text)`` — store a single fact. Mem0 runs the text
    through its fact-extraction LLM, embeds the resulting facts, and
    stores them.

Auth
----
The server expects ``Authorization: Token <MEM0_ADMIN_API_KEY>`` on every
request to ``/mcp``. The header value is the admin token from the Mem0
service; the same value is presented to Mem0 on every upstream call so
the MCP layer and the Mem0 layer share one gate. With
``MEM0_AUTH_DISABLED=true`` the value is ignored.

The token check happens in a Starlette ``BaseHTTPMiddleware`` mounted
around the MCP app. ``/health`` is intentionally exempt so Caddy (or any
external probe) can verify reachability without a token.

Why Streamable HTTP, not SSE
----------------------------
Streamable HTTP is the [recommended transport] as of MCP spec 2025-03-26.
It works cleanly behind Caddy/nginx because each request is a normal
HTTP POST; SSE-only servers keep long-lived connections that need extra
proxy configuration (``proxy_buffering off``, ``proxy_read_timeout``).
We expose the Streamable HTTP endpoint at ``/mcp`` and keep ``/sse`` as
an alias so older clients still work.

  [recommended transport]: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount

from mcp_server.mem0_client import Mem0Client, Mem0Error

logger = logging.getLogger("contextrealm.mcp")

# ─── Config from environment ────────────────────────────────────────────────

MEM0_BASE_URL = os.environ.get("MEM0_API_URL", "http://mem0:8000")
MEM0_USER_ID = os.environ.get("MEM0_USER_ID", "default")
MCP_TOKEN = os.environ.get("MEM0_ADMIN_API_KEY", "").strip()
LISTEN_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("MCP_PORT", "8765"))


# ─── FastMCP server and tool definitions ────────────────────────────────────

mcp = FastMCP("ContextRealm")

# Module-level handle so the auth middleware can share one HTTP client with
# the tools. (FastMCP tool functions read this at call time.)
_mem0: Mem0Client | None = None


@mcp.tool()
async def search_memories(query: str, limit: int = 5) -> list[str]:
    """Search the Realm's persistent memory for facts relevant to ``query``.

    Returns a list of human-readable memory strings. Empty list if no match
    or Mem0 is unreachable.
    """
    if _mem0 is None:
        return []
    try:
        results = await _mem0.search(query=query, limit=limit)
    except Mem0Error as exc:
        logger.warning("search_memories failed: %s", exc)
        return []
    return [_format_memory(m) for m in results if m]


@mcp.tool()
async def add_memory(text: str) -> int:
    """Store a single fact in the Realm's persistent memory.

    Returns the number of facts Mem0 extracted and stored (0 if the
    extraction LLM judged the input not worth remembering). Raises
    ``RuntimeError`` if Mem0 is unreachable so the client gets feedback.
    """
    if _mem0 is None:
        raise RuntimeError("MCP server not ready")
    try:
        stored = await _mem0.add(text=text)
    except Mem0Error as exc:
        logger.warning("add_memory failed: %s", exc)
        raise RuntimeError(f"Mem0 rejected the write: {exc}") from exc
    return len(stored)


def _format_memory(memory: dict) -> str:
    """Pull the human-readable text out of a Mem0 memory object.

    Mem0 returns ``memory``, ``text``, or ``content`` depending on the
    version and which field the fact-extraction LLM chose to populate.
    We try them in order and fall back to a serialised dict so the
    client never gets an empty string from a real hit.
    """
    for key in ("memory", "text", "content"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(memory).strip()


# ─── Auth middleware ───────────────────────────────────────────────────────


class _TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request to a non-exempt path without a valid token.

    The exempt paths are:
      * ``/health`` — liveness probe (no token, returns 200/503).
      * Anything not under ``/mcp`` or ``/sse`` — root responses.

    With ``MCP_TOKEN`` empty (the local-dev case with
    ``MEM0_AUTH_DISABLED=true``) the middleware is a no-op so the stack
    stays usable without secrets.
    """

    EXEMPT_PATHS = frozenset({"/health", "/", "/readyz"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # FastAPI/Starlette passes URL through request.url.path.
        path = request.url.path
        if path in self.EXEMPT_PATHS or not (path.startswith("/mcp") or path.startswith("/sse")):
            return await call_next(request)

        if not MCP_TOKEN:
            # Auth disabled. Same posture as MEM0_AUTH_DISABLED=true on the
            # Mem0 side — no upstream call will be authenticated either,
            # but we don't pretend to be checking anything here.
            return await call_next(request)

        header = request.headers.get("authorization", "")
        # Accept both "Token <key>" (Mem0-style) and "Bearer <key>"
        # (OAuth-style) so off-the-shelf MCP clients work without surgery.
        expected_token: str | None = None
        if header.startswith("Token "):
            expected_token = header[len("Token ") :]
        elif header.startswith("Bearer "):
            expected_token = header[len("Bearer ") :]

        if not expected_token or expected_token != MCP_TOKEN:
            logger.warning("Rejected MCP request to %s: invalid auth header", path)
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "detail": "Provide Authorization: Token <MEM0_ADMIN_API_KEY>",
                },
                status_code=401,
            )
        return await call_next(request)


# ─── App composition ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    """Initialise the shared Mem0 client on startup, close it on shutdown."""
    global _mem0
    auth_header = f"Token {MCP_TOKEN}" if MCP_TOKEN else None
    _mem0 = Mem0Client(
        base_url=MEM0_BASE_URL,
        authorization=auth_header,
        user_id=MEM0_USER_ID,
    )
    if not await _mem0.health():
        logger.warning(
            "Mem0 unreachable at %s on startup. The MCP server will keep "
            "running and retry on each request.",
            MEM0_BASE_URL,
        )
    else:
        logger.info("MCP server connected to Mem0 at %s", MEM0_BASE_URL)
    try:
        yield
    finally:
        if _mem0 is not None:
            await _mem0.aclose()
            _mem0 = None


async def _health_handler(_request: Request) -> Response:
    """Liveness probe. 200 if Mem0 is reachable, 503 otherwise.

    Auth-exempt on purpose: external monitoring should be able to verify
    reachability without the admin token.
    """
    if _mem0 is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    ok = await _mem0.health()
    payload = {"status": "ok" if ok else "degraded", "mem0_reachable": ok}
    return JSONResponse(payload, status_code=200 if ok else 503)


# FastMCP exposes two ASGI sub-apps; we mount both so older SSE clients
# keep working while new Streamable HTTP clients use /mcp.
_streamable_app = mcp.streamable_http_app()
_sse_app = mcp.sse_app()

app = Starlette(
    routes=[
        Mount("/mcp", app=_streamable_app),
        Mount("/sse", app=_sse_app),
    ],
    lifespan=lifespan,
)
# Starlette doesn't expose an easy way to add a free-floating route, so
# we hang the health endpoint off /health via a tiny ASGI shim.
app.add_route("/health", _health_handler, methods=["GET"])

# Wrap with auth. Adding middleware after routes is fine; Starlette runs
# outer middleware first on the way in.
app.add_middleware(_TokenAuthMiddleware)


# ─── Entrypoint ────────────────────────────────────────────────────────────


def main() -> None:
    """Run under uvicorn. ``MCP_HOST`` / ``MCP_PORT`` are read at startup."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Loud banner at boot so the dev sees what mode they're in.
    if MCP_TOKEN:
        logger.info("MCP auth: ENABLED (token length=%d)", len(MCP_TOKEN))
    else:
        logger.warning(
            "MCP auth: DISABLED — MCP_TOKEN is empty. Do not expose :%d publicly.", LISTEN_PORT
        )
    logger.info(
        "MCP server listening on %s:%d (Mem0=%s, user_id=%s)",
        LISTEN_HOST,
        LISTEN_PORT,
        MEM0_BASE_URL,
        MEM0_USER_ID,
    )
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info")


if __name__ == "__main__":
    main()
