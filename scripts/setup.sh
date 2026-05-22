#!/usr/bin/env bash
# =============================================================================
# ContextRealm — First-time setup
# =============================================================================
# Run once after `docker compose up -d` to pull the embedding model and
# enable the pgvector extension in Postgres.
#
# Usage:
#   bash scripts/setup.sh
# =============================================================================
set -euo pipefail

COMPOSE_PROJECT=contextrealm
OLLAMA_SERVICE="${COMPOSE_PROJECT}-ollama-1"
POSTGRES_SERVICE="${COMPOSE_PROJECT}-postgres-1"

EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-qwen3-embedding:0.6b}"
LLM_MODEL="${OLLAMA_LLM_MODEL:-}"
POSTGRES_DB="${POSTGRES_DB:-mem0_app}"

# ─── helpers ──────────────────────────────────────────────────────────────────
info()  { printf '\033[0;34m[setup]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[setup]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[setup]\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m[setup]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

# ─── pre-checks ───────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not installed or not in PATH"

if ! docker ps --format '{{.Names}}' | grep -q "^${OLLAMA_SERVICE}$"; then
    die "Container '${OLLAMA_SERVICE}' is not running. Run 'docker compose up -d' first."
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${POSTGRES_SERVICE}$"; then
    die "Container '${POSTGRES_SERVICE}' is not running. Run 'docker compose up -d' first."
fi

# ─── Ollama: pull embedding model ─────────────────────────────────────────────
info "Pulling embedding model: ${EMBEDDING_MODEL}"
docker exec "${OLLAMA_SERVICE}" ollama pull "${EMBEDDING_MODEL}"
ok "Embedding model ready: ${EMBEDDING_MODEL}"

# ─── Ollama: pull optional local LLM ──────────────────────────────────────────
if [ -n "${LLM_MODEL}" ]; then
    info "Pulling local LLM: ${LLM_MODEL}"
    docker exec "${OLLAMA_SERVICE}" ollama pull "${LLM_MODEL}"
    ok "Local LLM ready: ${LLM_MODEL}"
else
    warn "OLLAMA_LLM_MODEL is not set — skipping local LLM pull."
    warn "Set it in .env if you want a local fallback model."
fi

# ─── Postgres: enable pgvector extension ──────────────────────────────────────
info "Enabling pgvector extension in database '${POSTGRES_DB}'"
docker exec "${POSTGRES_SERVICE}" \
    psql -U postgres -d "${POSTGRES_DB}" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;"
ok "pgvector extension enabled"

# ─── done ─────────────────────────────────────────────────────────────────────
ok "Setup complete. Open http://localhost:3000 to get started."
