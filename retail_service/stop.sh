#!/usr/bin/env bash
# 只停止本服务：按监听端口找到进程（默认 18100），不会误伤其他服务。
PORT="${PORT:-18100}"
PID="$(ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
[ -n "$PID" ] && kill "$PID" && echo "已停止 pid=$PID (:${PORT})" || echo "端口 ${PORT} 上没有运行中的服务"
