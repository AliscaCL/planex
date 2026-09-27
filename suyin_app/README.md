# 溯因 · 内存根因分析 — deepagents + Backend + UI（独立应用）

```bash
cd suyin_app
./run.sh          # 默认 http://localhost:8801
```

- `agent.py`：deepagents 智能体 + 工具
- `server.py`：FastAPI + SSE（`/`、`/api/chat`、`/api/reset`）
- `static/index.html`：对话式 UI
- `.env`：LLM 配置（默认 DeepSeek V4，可改）
- `.venv`：独立虚拟环境

切换 provider 改 `.env`；工具返回确定性演示数据，可在 `agent.py` 替换真实数据源。
