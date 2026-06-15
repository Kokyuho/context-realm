# Contributing to ContextRealm

Thank you for your interest in contributing. This document covers the development workflow, code standards, and process for submitting changes.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Reporting Bugs](#reporting-bugs)
3. [Suggesting Features](#suggesting-features)
4. [Development Setup](#development-setup)
5. [Project Structure](#project-structure)
6. [Code Style](#code-style)
7. [Pull Request Process](#pull-request-process)
8. [Release Process](#release-process)

---

## Code of Conduct

Be respectful and constructive. Harassment, personal attacks, and disruptive behaviour will not be tolerated. If you experience or witness unacceptable behaviour, open a confidential issue or contact the maintainers directly.

---

## Reporting Bugs

Use the **Bug Report** issue template. Include:

- ContextRealm version / commit hash
- Host OS and Docker version
- Steps to reproduce the issue
- Expected vs. actual behaviour
- Relevant logs (`docker compose logs <service> --tail=100`)

Do **not** include secrets, API keys, or passwords in issues.

---

## Suggesting Features

Use the **Feature Request** issue template. Describe:

- The problem you are trying to solve
- Your proposed solution
- Any alternatives you have considered

Features aligned with the project's core goals (private, persistent, composable AI memory) are most likely to be accepted.

---

## Development Setup

### Prerequisites

The same as the user setup — see [docs/setup.md#prerequisites](setup.md#prerequisites).

Additionally:

- Python 3.11+ (for working on `pipeline/` and `scripts/`)
- `ruff` for linting: `pip install ruff`

### Clone and configure

```bash
git clone https://github.com/yourusername/context-realm.git
cd context-realm
cp .env.example .env
# Edit .env — add test API keys and set passwords
```

### Start the development stack

```bash
docker compose build
docker compose up -d
bash scripts/setup.sh
```

### Working on the Pipeline

The pipeline runs inside the `pipelines` container. During development, mount the source file directly to avoid rebuilding on every change:

```bash
# In docker-compose.yml, uncomment the dev volume mount under the pipelines service
# (see the inline comment in that file)
docker compose restart pipelines
```

Pipelines reload automatically when the mounted file changes.

### Running lints

First-time setup — install the dev tooling into your local environment. The same file is what CI installs, so what works locally will work in the PR gate.

```bash
pip install -r requirements-dev.txt
```

Then, at any time:

```bash
# Python (pipeline + scripts + tests)
ruff check pipeline/ scripts/ tests/ pyproject.toml

# Format check
ruff format --check pipeline/ scripts/ tests/ pyproject.toml
```

### Running tests

The test suite is split into two tiers so the default run stays fast and works on a clean checkout.

| Tier           | Path                  | Requires                                    | Run on PR? |
| -------------- | --------------------- | ------------------------------------------- | ---------- |
| **Unit**       | `tests/unit/`         | Nothing — pure Python                       | Yes        |
| **Integration**| `tests/integration/`  | The Docker stack (`docker compose up -d`)   | No (manual)|

Integration tests are skipped by default and tagged with `@pytest.mark.integration`. A stack-up guard fixture in `tests/integration/conftest.py` skips the whole suite with a clear message if Mem0 is unreachable — so a missing service is reported as "skipped", not as a confusing connection error.

```bash
# Run all unit tests (default; safe on a fresh checkout)
bash scripts/test.sh

# Run the full suite — unit + integration. Requires the Docker stack.
bash scripts/test.sh --integration

# Also run ruff as part of the test command
bash scripts/test.sh --lint

# Forward any pytest flag (e.g. -k, -x, --co)
bash scripts/test.sh -k embedding -x
```

`scripts/test.sh` creates a local `.venv/` on first run and installs `requirements-dev.txt`. Subsequent runs reuse it.

#### Writing a new test

- **Unit test** — drop a `test_*.py` in `tests/unit/`. Use fixtures from `tests/conftest.py`. No external services.
- **Integration test** — drop a `test_*.py` in `tests/integration/`. The marker is added automatically by `tests/integration/conftest.py`. Use the `unique_user_id` fixture to avoid cross-test contamination.
- **Need a new Mem0 helper?** Add it to `tests/helpers/mem0_client.py` so unit tests can mock the client and integration tests can call it directly.

The `mem0_client` fixture in `tests/conftest.py` is the single place to mock when unit-testing the future pipeline.

---

## Project Structure

```
context-realm/
├── docker/
│   └── mem0-server/        # Mem0 image — patched at build time from upstream
│       └── Dockerfile
├── pipeline/
│   ├── mem0_filter.py      # Core pipeline logic (inlet/outlet hooks)
│   ├── requirements.txt    # Pipeline Python dependencies
│   └── Dockerfile
├── config/
│   └── litellm_config.yaml # Model definitions
├── scripts/
│   ├── setup.sh            # First-time setup
│   ├── test.sh             # Test runner wrapper around pytest
│   └── import_context.py   # Bulk knowledge import
├── tests/                  # Test suite (see "Running tests" below)
│   ├── unit/               # Pure Python, no external services
│   ├── integration/        # Hits live Mem0, Ollama, Postgres
│   ├── helpers/            # Shared utilities (Mem0 REST client, etc.)
│   └── conftest.py         # Root fixtures (env loading, clients)
├── helm/                   # Kubernetes Helm chart
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
├── docs/
│   ├── architecture.md
│   ├── setup.md
│   └── contributing.md     ← you are here
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/          # CI/CD
├── docker-compose.yml
├── docker-compose.prod.yml
├── pyproject.toml          # Test + lint configuration
├── requirements-dev.txt    # Dev-only Python deps (pytest, ruff, httpx)
└── .env.example
```

### Where to make changes

| What you want to change        | File to edit                          |
| ------------------------------ | ------------------------------------- |
| Memory retrieval/storage logic | `pipeline/mem0_filter.py`             |
| Add or change AI models        | `config/litellm_config.yaml`          |
| Mem0 build patches             | `docker/mem0-server/Dockerfile`       |
| Local service configuration    | `docker-compose.yml`                  |
| Production Helm configuration  | `helm/values.yaml`, `helm/templates/` |
| Bulk import tool               | `scripts/import_context.py`           |
| Tests                          | `tests/` (see "Running tests")        |
| Lint / test config             | `pyproject.toml`                      |
| Documentation                  | `docs/`                               |

---

## Code Style

### Python

- Format with `ruff format` (line length 100)
- Lint with `ruff check`
- No type annotations required for scripts; use them in `mem0_filter.py`
- No docstrings required unless a function's purpose is genuinely non-obvious

Configuration lives in `pyproject.toml`.

### YAML / Dockerfiles / Shell

- 2-space indentation for YAML
- Shell scripts must pass `shellcheck`
- Dockerfile: multi-stage builds, pinned base images, no `latest` tags

### General

- Keep commits focused. One logical change per commit.
- Write commit messages in the imperative mood: _"Add ollama embedder support"_, not _"Added..."_
- Do not commit `.env` or any file containing secrets

---

## Pull Request Process

1. **Fork** the repository and create your branch from `main`:

   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make your changes** following the code style guidelines above.

3. **Test your changes** end-to-end with the local Docker Compose stack.

4. **Open a Pull Request** against the `main` branch. Fill in the PR template, including:
   - What the PR does
   - How to test it
   - Any breaking changes

5. **Address review feedback.** PRs require at least one maintainer approval before merge.

6. **Squash or rebase** to clean history before final merge if requested.

### PR scope guidelines

- Keep PRs focused. Separate unrelated improvements into separate PRs.
- Infrastructure changes (Helm, CI) and application changes (pipeline, scripts) should be separate PRs when possible.
- Breaking changes to the `.env.example` format or `docker-compose.yml` service names require a major version bump and must be documented in the PR description.

---

## Release Process

Releases follow [Semantic Versioning](https://semver.org/):

- **PATCH** (0.0.x): bug fixes, documentation
- **MINOR** (0.x.0): new features, backward-compatible changes
- **MAJOR** (x.0.0): breaking changes (env var renames, service topology changes)

To cut a release:

1. Update `MEM0_VERSION` in `.env.example` if the Mem0 upstream version has changed
2. Tag the commit: `git tag -a v0.2.0 -m "Release v0.2.0"`
3. Push the tag: `git push origin v0.2.0`
4. The CI workflow builds and pushes images tagged with the version
5. Create a GitHub Release with a changelog describing changes since the last release
