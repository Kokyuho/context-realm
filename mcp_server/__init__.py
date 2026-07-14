"""ContextRealm — MCP server.

A thin MCP-over-HTTP server that exposes two tools over the Mem0 REST API:
``search_memories`` and ``add_memory``. Auth is enforced via the same admin
token Mem0 uses internally, so the MCP endpoint and the REST API share one
gate.

Why a custom server instead of an upstream image:
  * We need to enforce the same admin token Mem0 already enforces.
  * The two endpoints we expose are tiny — a full image is overkill.
  * Keeping the surface in-tree lets us evolve the protocol with the rest
    of the project.

The package is named ``mcp_server`` rather than ``mcp`` to avoid shadowing
the official ``mcp`` Python SDK we depend on.
"""

__version__ = "0.1.0"
