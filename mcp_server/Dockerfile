# =============================================================================
# ContextRealm — MCP server image
# =============================================================================
# A small Python service that exposes Mem0 over the Model Context Protocol.
# Listens on :8765 by default; bound to 127.0.0.1 in docker-compose so the
# only path in is through the Caddy sidecar.
# =============================================================================

FROM python:3.12-slim AS base

# libpq5 is needed by psycopg (transitively pulled in by Mem0 client code
# that the MCP server imports). Slim images don't include it.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash mcp

WORKDIR /app

# Copy *into* a subdirectory so the package layout matches what the source
# code expects (``from mcp_server.mem0_client import ...``). The build
# context is ./mcp_server/, so we COPY its contents into /app/mcp_server/.
COPY requirements.txt /app/mcp_server/requirements.txt
COPY --chown=mcp:mcp . /app/mcp_server/

# Install the package. ``pip install .`` is overkill; instead we add
# /app/mcp_server to PYTHONPATH so the source imports resolve, and
# install the third-party deps via requirements.txt.
RUN pip install --no-cache-dir -r /app/mcp_server/requirements.txt

USER mcp

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/mcp_server \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765

EXPOSE 8765

CMD ["python", "-m", "mcp_server.server"]