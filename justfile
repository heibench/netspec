# Python (uv) task runner

set dotenv-load := false

# Default: show available recipes
default:
    @just --list

# Install dependencies and set up environment.
#
# --all-extras deliberately: without it the `mcp` extra is absent, pyright cannot
# resolve the MCP module, and its tests skip silently -- CI reported green while
# covering none of that surface.
setup:
    uv sync --all-extras

# Format code (mutates working tree — use locally)
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Verify formatting (non-mutating — use in CI)
fmt-check:
    uv run ruff format --check .

# Run linters
lint:
    uv run ruff check .

# Type-check
typecheck:
    uv run pyright

# Format + lint + type-check (non-mutating — safe for CI)
check: fmt-check lint typecheck

# Run tests
test:
    uv run pytest

# Run main entrypoint
run:
    uv run python -m kicad_netspec

# Remove build artifacts
clean:
    rm -rf .venv dist .pytest_cache .ruff_cache .pyright
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Verify the KiCad API facts this project plans around (docs/DECISIONS.md D6)
assumptions kicad_source:
    uv run pytest tests/test_kicad_assumptions.py -v --kicad-source={{kicad_source}}

# Audit every package pinned in uv.lock against OSV (uv's experimental `uv audit`).
audit:
    uv audit --frozen --preview-features audit-command
