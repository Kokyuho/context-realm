#!/usr/bin/env bash
# =============================================================================
# ContextRealm — Test runner
# =============================================================================
# Wraps pytest with the right flags and a virtualenv bootstrap.
#
# Usage:
#   bash scripts/test.sh                  # unit tests only (default; safe on any machine)
#   bash scripts/test.sh --integration    # unit + integration (requires Docker stack)
#   bash scripts/test.sh --lint           # also run ruff check + format check
#   bash scripts/test.sh -k embedding     # forward any other pytest arg
#
# First run will create a local venv at .venv/ and install requirements-dev.txt.
# Subsequent runs reuse it.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${REPO_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# ─── helpers ──────────────────────────────────────────────────────────────────
info()  { printf '\033[0;34m[test]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[test]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[test]\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m[test]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

# ─── venv setup ───────────────────────────────────────────────────────────────
if [ ! -d "${VENV}" ]; then
    info "Creating virtualenv at ${VENV}"
    "${PYTHON_BIN}" -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

# Install only if the marker file is older than requirements-dev.txt — keeps
# the per-run cost to a simple stat comparison.
MARKER="${VENV}/.requirements-dev.installed"
REQ="${REPO_ROOT}/requirements-dev.txt"
if [ ! -f "${MARKER}" ] || [ "${REQ}" -nt "${MARKER}" ]; then
    info "Installing dev dependencies"
    pip install --quiet --upgrade pip
    pip install --quiet -r "${REQ}"
    touch "${MARKER}"
fi

# ─── argument parsing ─────────────────────────────────────────────────────────
RUN_INTEGRATION=0
RUN_LINT=0
PYTEST_ARGS=()

for arg in "$@"; do
    case "${arg}" in
        --integration) RUN_INTEGRATION=1 ;;
        --lint)        RUN_LINT=1 ;;
        -h|--help)
            sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) PYTEST_ARGS+=("${arg}") ;;
    esac
done

# ─── lint (optional) ──────────────────────────────────────────────────────────
if [ "${RUN_LINT}" -eq 1 ]; then
    info "Running ruff check"
    ruff check pipeline/ scripts/ tests/ mcp_server/ pyproject.toml

    info "Running ruff format --check"
    ruff format --check pipeline/ scripts/ tests/ mcp_server/ pyproject.toml
fi

# ─── pytest ───────────────────────────────────────────────────────────────────
cd "${REPO_ROOT}"

# Use the array expansion that survives an empty array under `set -u`.
# (Plain "${PYTEST_ARGS[@]}" fails when the array is empty under nounset.)
if [ "${RUN_INTEGRATION}" -eq 1 ]; then
    info "Running unit + integration tests"
    ok "Integration tests require the Docker stack: docker compose up -d"
    # Override the default `-m not integration` addopt so the integration
    # suite actually executes when --integration is passed.
    pytest -m "" tests/ "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"
else
    info "Running unit tests only (integration skipped)"
    info "Use --integration to also run the live Mem0 suite"
    pytest "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"
fi

ok "Tests passed"
