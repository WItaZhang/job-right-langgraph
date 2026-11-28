#!/bin/bash
# Project "done" standard: lint, formatting and the test suite, against the committed lockfile.
set -e
cd "$(dirname "$0")/.."
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
"$UV" run --locked ruff check .
"$UV" run --locked ruff format --check .
"$UV" run --locked pytest -q
