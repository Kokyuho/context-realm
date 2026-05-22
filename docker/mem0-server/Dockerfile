# =============================================================================
# ContextRealm — Mem0 Server Image
# =============================================================================
# Builds the upstream Mem0 server from source and applies minimal patches to
# support Ollama as a local embedder provider.
#
# No Mem0 source code lives in this repository. The upstream is cloned inside
# the Docker build context from the pinned tag set by MEM0_VERSION.
#
# To upgrade Mem0:
#   1. Update MEM0_VERSION in .env (and .env.example)
#   2. Run: docker compose build mem0
#   3. Verify the /providers endpoint still lists "ollama"
# =============================================================================

# ─── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ARG MEM0_VERSION=v2.0.2

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    git clone --depth 1 --branch ${MEM0_VERSION} \
        https://github.com/mem0ai/mem0 /mem0 && \
    apt-get purge -y --auto-remove git && \
    rm -rf /var/lib/apt/lists/*

# ── Patch 1: add the Ollama Python client to server dependencies ──────────────
RUN echo "ollama>=0.4.0" >> /mem0/server/requirements.txt

# ── Patch 2: register "ollama" as a bundled embedder provider ─────────────────
# v2.0.2 uses a tuple: BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")
# Without this patch, POSTing embedder.provider = "ollama" to /configure
# returns HTTP 400 ("provider not bundled in this image").
#
# Guard: fail the build immediately if the symbol is not found so a future
# upstream rename surfaces as a build error rather than a silent runtime bug.
RUN grep -q 'BUNDLED_EMBEDDER_PROVIDERS' /mem0/server/main.py || { \
        echo "BUILD ERROR: BUNDLED_EMBEDDER_PROVIDERS not found in main.py."; \
        echo "The upstream format may have changed for ${MEM0_VERSION}."; \
        echo "Review docker/mem0-server/Dockerfile and update the patch."; \
        exit 1; \
    }

RUN sed -i \
    's/BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")/BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini", "ollama")/' \
    /mem0/server/main.py

# Guard: verify the patch actually applied
RUN grep -q '"ollama"' /mem0/server/main.py || { \
        echo "BUILD ERROR: Ollama patch did not apply."; \
        echo "The exact BUNDLED_EMBEDDER_PROVIDERS literal may have changed in ${MEM0_VERSION}."; \
        echo "Check the current value in upstream server/main.py and update the sed pattern."; \
        exit 1; \
    }

# ── Patch 3: make embedder and LLM providers configurable ───────────────────
# Upstream DEFAULT_CONFIG hardcodes provider="openai" for both the embedder and
# the LLM, crashing at startup when OPENAI_API_KEY is absent.  This patch adds
# env-var hooks so both can be configured from docker-compose.yml without
# modifying any upstream source files.
#
# Embedder: MEM0_DEFAULT_EMBEDDER_PROVIDER (default: ollama)
#           OLLAMA_EMBEDDING_MODEL          (default: qwen3-embedding:0.6b)
#           OLLAMA_BASE_URL                 (default: http://ollama:11434)
# LLM:      MEM0_DEFAULT_LLM_PROVIDER       (default: openai)
#           MEM0_LLM_API_KEY                (falls back to OPENAI_API_KEY)
COPY patch3.py /tmp/patch3.py
RUN python3 /tmp/patch3.py && rm /tmp/patch3.py

RUN pip install --no-cache-dir -r /mem0/server/requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

# libpq5 is required by psycopg (the pure-python fallback also needs it).
# python:3.12-slim does not include it.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Run as a non-root user
RUN useradd --create-home --shell /bin/bash mem0

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /mem0/server /app

# Entrypoint: run DB migrations before starting the API server.
# Uses `exec "$@"` so the uvicorn process receives OS signals correctly.
RUN printf '#!/bin/sh\nset -e\nalembic upgrade head\nexec "$@"\n' > /entrypoint.sh && \
    chmod +x /entrypoint.sh

RUN mkdir -p /app/history && chown -R mem0:mem0 /app

WORKDIR /app
USER mem0

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# No --reload in production
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
