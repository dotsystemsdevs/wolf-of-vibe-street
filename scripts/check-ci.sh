#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml — run before your push.
#   ./scripts/check-ci.sh
# To auto-fix formatting: uv run ruff format .
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "== uv sync =="
uv sync
echo "== ruff check =="
uv run ruff check .
echo "== ruff format --check =="
uv run ruff format --check .
echo "== pytest + coverage =="
uv run pytest --cov --cov-report=term-missing
echo "== All CI steps passed. =="
