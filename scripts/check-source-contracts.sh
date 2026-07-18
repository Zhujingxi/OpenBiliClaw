#!/usr/bin/env bash
set -euo pipefail

uv run --frozen pytest -q tests/vnext/sources \
  --cov \
  --cov-config=.coveragerc.sources \
  --cov-report=term-missing
