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

```bash
# Python (pipeline + scripts)
ruff check pipeline/ scripts/

# Format check
ruff format --check pipeline/ scripts/
```

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
│   └── import_context.py   # Bulk knowledge import
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
| Documentation                  | `docs/`                               |

---

## Code Style

### Python

- Format with `ruff format` (line length 100)
- Lint with `ruff check`
- No type annotations required for scripts; use them in `mem0_filter.py`
- No docstrings required unless a function's purpose is genuinely non-obvious

Configuration lives in `pyproject.toml` (to be added).

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
