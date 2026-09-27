#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8801}" "$@"
