# Contributing

## Prerequisites

Install [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Dev setup

```bash
# Clone and enter the repo
git clone https://github.com/HuntIntegrativeSolutions/daedalus.git
cd daedalus

# Install all dependencies (base + typed + oracle extras + dev tools)
uv sync --extra typed --extra oracle

# Install pre-commit hooks
uv run pre-commit install
```

## Running the checks

```bash
# Tests (includes sans-I/O firewall enforcement)
uv run pytest

# Lint
uv run ruff check .

# Auto-fix lint issues
uv run ruff check --fix .

# Format
uv run ruff format .

# Format check (CI mode)
uv run ruff format --check .

# Type check
uv run mypy
```

## Branch and PR flow

1. Branch off `main`: `git checkout -b <type>/<short-description>`
2. Make changes; keep commits atomic.
3. Ensure all checks pass locally before pushing.
4. Open a PR against `main`. CI must be green on Python 3.11, 3.12, and 3.13.
5. Update `CHANGELOG.md` under `[Unreleased]`.

## The sans-I/O rule

Layers L0–L3 (`cip/`, `packets/`, `session/`, `drivers/`) must **never** import
`socket`, `ssl`, `asyncio`, `anyio`, `selectors`, `socketserver`, `http`, `urllib`,
or `requests` — directly or transitively. Sockets live exclusively in `transport/`.

`tests/test_sans_io_firewall.py` enforces this in CI via AST inspection. The test
is parametrized per source file; a violation surfaces immediately with the exact
file and forbidden import.

## Commit convention

```
<type>(<scope>): <short summary>

<optional body>
```

Types: `feat`, `fix`, `test`, `docs`, `chore`, `ci`, `refactor`, `perf`

Examples:
- `feat(cip): add DINT encode/decode round-trip`
- `fix(session): handle null session ID on forward close`
- `chore(deps): bump anyio to 4.5`

## Parity oracle

`pycomm3` and `pylogix` are installed via the `[oracle]` extra and may be imported
**only from `tests/`**. They must never be imported from `src/daedalus/` and must
never be vendored as source into this repository.
