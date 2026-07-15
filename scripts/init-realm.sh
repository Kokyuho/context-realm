#!/usr/bin/env bash
# =============================================================================
# ContextRealm — Realm initialiser
# =============================================================================
# First-run helper that gets a fresh checkout to "docker compose up -d"
# without any manual .env editing. Idempotent: safe to re-run.
#
# What it does:
#   1. Copy .env.example → .env if .env does not exist.
#   2. Generate a cryptographically random MEM0_ADMIN_API_KEY if the
#      placeholder is still empty (this is the token Mem0 enforces and
#      that MCP clients will need; rotating it rotates the Realm).
#   3. Optionally `docker compose up -d` so the user can `git clone … &&
#      bash scripts/init-realm.sh` and end up with a running stack.
#   4. Optionally run scripts/setup.sh (Ollama model pull + pgvector).
#
# Usage:
#   bash scripts/init-realm.sh                # just .env + token
#   bash scripts/init-realm.sh --up           # also bring up the stack
#   bash scripts/init-realm.sh --up --models   # also pull Ollama models
#   bash scripts/init-realm.sh --help
#
# Exit codes:
#   0   success
#   1   pre-check failed (missing tool, etc.)
#   2   .env.example missing (cannot bootstrap)
# =============================================================================
set -euo pipefail

# ─── Paths ──────────────────────────────────────────────────────────────────
# The script's "realm" is always the current working directory. Tests rely
# on this so they can copy a synthetic .env.example into tmp_path and
# invoke the script there. In normal use, the operator's shell is at the
# repo root and behaviour is the same.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(pwd)"
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
SETUP_SH="${SCRIPT_DIR}/setup.sh"

# ─── Defaults / flags ──────────────────────────────────────────────────────
DO_UP=0
DO_MODELS=0
PRINT_HELP=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/init-realm.sh [options]

Options:
  --up              Bring the Docker stack up after preparing .env.
  --models          Run scripts/setup.sh (Ollama models, pgvector) after up.
  --help            Print this help and exit.

Default behaviour (no flags): copy .env.example → .env and generate a
MEM0_ADMIN_API_KEY if one is not already set. Idempotent; safe to re-run.
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --up)      DO_UP=1 ;;
        --models)  DO_MODELS=1 ;;
        --help|-h) PRINT_HELP=1 ;;
        *) printf '\033[0;31m[init]\033[0m unknown option: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

if [ "${PRINT_HELP}" -eq 1 ]; then
    usage
    exit 0
fi

# ─── Output helpers ────────────────────────────────────────────────────────
info()  { printf '\033[0;34m[init]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[init]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[init]\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m[init]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

# ─── Pre-checks ────────────────────────────────────────────────────────────
[ -f "${ENV_EXAMPLE}" ] || die ".env.example not found at ${ENV_EXAMPLE}"

# We only hard-require docker for the --up flag; everything else is plain
# POSIX so even a fresh checkout without Docker can use this script to
# prepare a .env that the operator will run on a different host later.
NEED_DOCKER=0
if [ "${DO_UP}" -eq 1 ] || [ "${DO_MODELS}" -eq 1 ]; then
    NEED_DOCKER=1
fi
if [ "${NEED_DOCKER}" -eq 1 ]; then
    command -v docker >/dev/null 2>&1 || die "docker is not installed or not in PATH"
    docker info >/dev/null 2>&1 || die "docker daemon is not reachable. Start Docker and retry."
fi

# ─── Step 1: .env from example ─────────────────────────────────────────────
if [ -f "${ENV_FILE}" ]; then
    info ".env already exists — leaving it untouched."
else
    info "Creating .env from .env.example"
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    ok ".env created. Review it before deploying; you'll likely want to set:"
    ok "  REALM_DOMAIN    public hostname for Let's Encrypt (production only)"
    ok "  LITELLM_API_KEY_<model>  keys for any frontier models you enable"
fi

# Use a temp file for atomic writes so we don't end up with a half-written
# .env if the operator hits Ctrl-C mid-rotation.
ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
trap 'rm -f "${ENV_TMP}"' EXIT

# ─── Step 2: rotate MEM0_ADMIN_API_KEY if empty ────────────────────────────
# Pull the current value out of .env without executing it. Empty value or
# literal placeholder both trigger generation.
CURRENT_KEY="$(grep -E '^[[:space:]]*MEM0_ADMIN_API_KEY[[:space:]]*=' "${ENV_FILE}" 2>/dev/null \
    | head -n1 \
    | sed -E 's/^[[:space:]]*MEM0_ADMIN_API_KEY[[:space:]]*=[[:space:]]*//' \
    | sed -E 's/[[:space:]]*$//' \
    | sed -E 's/^["'\''](.*)["'\'']$/\1/')"

if [ -z "${CURRENT_KEY}" ]; then
    if command -v openssl >/dev/null 2>&1; then
        NEW_KEY="$(openssl rand -hex 32)"
    elif command -v python3 >/dev/null 2>&1; then
        NEW_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    else
        die "Neither openssl nor python3 is available; set MEM0_ADMIN_API_KEY in .env by hand."
    fi
    info "Generating a fresh MEM0_ADMIN_API_KEY (32 bytes, hex)"
    # Replace the placeholder line in place. If for some reason the line is
    # missing, append it.
    if grep -qE '^[[:space:]]*MEM0_ADMIN_API_KEY[[:space:]]*=' "${ENV_FILE}"; then
        awk -v key="${NEW_KEY}" '
            /^[[:space:]]*MEM0_ADMIN_API_KEY[[:space:]]*=/ { print "MEM0_ADMIN_API_KEY=" key; next }
            { print }
        ' "${ENV_FILE}" > "${ENV_TMP}"
        mv "${ENV_TMP}" "${ENV_FILE}"
    else
        # Restore trap target (awk removed the temp file).
        printf '\nMEM0_ADMIN_API_KEY=%s\n' "${NEW_KEY}" >> "${ENV_FILE}"
    fi
    ok "MEM0_ADMIN_API_KEY generated. Save this — clients will need it as Authorization: Token <key>."
else
    info "MEM0_ADMIN_API_KEY already populated — leaving it untouched."
fi

# ─── Step 3: optional docker compose up ────────────────────────────────────
if [ "${DO_UP}" -eq 1 ]; then
    info "Bringing the stack up. First run will pull several images; expect 5–10 min."
    (
        cd "${REPO_ROOT}"
        docker compose pull
        docker compose up -d
    )
    ok "Stack is up. Run 'docker compose ps' to see the services."
fi

# ─── Step 4: optional model pull + pgvector ────────────────────────────────
if [ "${DO_MODELS}" -eq 1 ]; then
    if [ ! -f "${SETUP_SH}" ]; then
        warn "scripts/setup.sh not found; skipping model pull and pgvector init."
    elif [ "${DO_UP}" -eq 0 ]; then
        warn "--models implies the stack is already up; skipping without --up."
    else
        info "Running scripts/setup.sh (Ollama models + pgvector)"
        bash "${SETUP_SH}"
    fi
fi

# ─── Done ──────────────────────────────────────────────────────────────────
ok "Realm initialised."
ok "Next steps:"
ok "  1. Review .env and set REALM_DOMAIN (and any frontier model keys) for production."
ok "  2. If you ran --up, open http://localhost:3000 and start chatting."
ok "  3. If you want MCP access from Claude Desktop / Cursor, see docs/setup.md#mcp-configuration."
