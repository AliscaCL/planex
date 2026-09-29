#!/usr/bin/env bash
# 启动零售客服 Agent 服务（前后端一体）。默认端口 18100，可用 PORT 覆盖。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-18100}" "$@"
