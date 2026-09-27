"""单场景 FastAPI 后端：导入本目录的 agent 模块，提供 UI 与 SSE 流式接口。

  GET /            聊天 UI
  GET /assets/*    图表静态资源（若存在）
  GET /api/reset   重置会话
  GET /api/chat    SSE：?session=..&message=..
"""
from __future__ import annotations
import os, json, pathlib
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

HERE = pathlib.Path(__file__).parent
STATIC = HERE / "static"


def _load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from llm import get_model       # noqa: E402
import agent as scenario        # noqa: E402  本目录的场景 agent 模块

MODEL = get_model()
ARTIFACTS = getattr(scenario, "ARTIFACTS", {})
_sessions: dict[str, object] = {}


def get_agent(session: str):
    if session not in _sessions:
        a, _state = scenario.make_agent(MODEL)
        _sessions[session] = a
    return _sessions[session]


def _chunk_blocks(chunk):
    out = []
    c = getattr(chunk, "content", None)
    if isinstance(c, str):
        if c:
            out.append(("token", c))
    elif isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t in ("text", "text_delta"):
                out.append(("token", b.get("text", "")))
            elif t in ("thinking", "thinking_delta"):
                out.append(("think", b.get("thinking", "") or b.get("text", "")))
    return out


def _tool_output_text(out) -> str:
    if hasattr(out, "content"):
        out = out.content
    if isinstance(out, list):
        out = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in out)
    return str(out)


app = FastAPI()
_assets = STATIC / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/reset")
def reset(session: str):
    _sessions.pop(session, None)
    return {"ok": True}


@app.get("/api/chat")
async def chat(session: str, message: str, request: Request):
    agent = get_agent(session)
    display = getattr(agent, "_display_tools", set())
    config = {"configurable": {"thread_id": "main"}, "recursion_limit": 80}

    async def gen():
        try:
            async for ev in agent.astream_events(
                {"messages": [{"role": "user", "content": message}]}, config=config, version="v2"):
                if await request.is_disconnected():
                    break
                kind = ev["event"]
                if kind == "on_chat_model_stream":
                    for typ, txt in _chunk_blocks(ev["data"]["chunk"]):
                        if txt:
                            yield {"data": json.dumps({"type": typ, "text": txt})}
                elif kind == "on_tool_start":
                    name = ev.get("name", "")
                    if name in display:
                        args = ev["data"].get("input") or {}
                        yield {"data": json.dumps({"type": "tool_start", "name": name, "args": args})}
                elif kind == "on_tool_end":
                    name = ev.get("name", "")
                    if name in display:
                        text = _tool_output_text(ev["data"].get("output"))
                        yield {"data": json.dumps({"type": "tool_end", "name": name, "output": text})}
                        if name in ARTIFACTS:
                            kindx, url, cap = ARTIFACTS[name]
                            yield {"data": json.dumps({"type": "artifact", "kind": kindx,
                                                       "url": url, "cap": cap, "name": name})}
        except Exception as e:  # noqa: BLE001
            yield {"data": json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"})}
        yield {"data": json.dumps({"type": "done"})}

    return EventSourceResponse(gen())
