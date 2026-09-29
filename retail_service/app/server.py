"""零售客服 Agent —— 前后端一体的服务。

Agent 组装方式参考 planex-ie3/bench 的 light-skill 臂：
    PlanexAgent（harness-planex）+ retail_skill（Agent Skill）+ DeepSeek，
    业务数据是 τ-bench retail 的 users / orders / products。

接口：
    GET  /                    前端页面
    POST /api/key             保存 DeepSeek API Key（仅存服务端内存，先向 DeepSeek 校验）
    GET  /api/key/status      查询是否已配置（只返回布尔值，绝不回传 Key）
    DELETE /api/key           清除 Key
    POST /api/chat            SSE：把一句话交给 Agent，流式返回 思考/工具调用/结果/答案卡片
    POST /api/reset           丢弃一个会话（工作目录 + 业务数据副本）
    GET  /api/state           本会话发生过的写操作流水
    GET  /api/table           把会话里的 csv/json 表格读成 {columns, rows}（供 dataframe 卡片）
    GET  /api/file            读取会话里的文件（供 figure 卡片）

API Key 只出现在：浏览器输入框（保存后即清空并显示 ****）→ 一次 HTTPS/HTTP 请求体 → 服务端内存。
不写盘、不进日志、不进 URL、不回传。
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

import httpx
import retail_skill
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from planex.core import AIClient, ExpertConfig, ModelConfig, PlanexAgent, RuntimeConfig
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
SESSION_ROOT = ROOT / "sessions"
TABLES = ("orders", "products", "users")

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
#: bench/runconfig.py 实测可用的两个模型。flash 更快，pro 更稳。
MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
DEFAULT_MODEL = MODELS[0]
REASONING_EFFORT = "medium"
RECURSION_LIMIT = 120          # bench 实测：默认 50 在多步回答上会 GraphRecursionError
MAX_RESULT_CHARS = 8000        # 单条工具结果转发给前端的上限

SESSION_ROOT.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("retail_service")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

app = FastAPI(title="Retail Agent Service")

# ---------------------------------------------------------------------------
# API Key 仓库：client_id -> key，仅内存。
# ---------------------------------------------------------------------------
_KEYS: dict[str, str] = {}
_AGENTS: dict[str, PlanexAgent] = {}       # sha256(key)[:16] -> 已 ainit 的 Agent
_AGENT_LOCK = asyncio.Lock()


def _check_id(value: str, what: str) -> str:
    if not _ID.match(value or ""):
        raise HTTPException(400, f"{what} 不合法")
    return value


def _scrub(text: str, key: str | None) -> str:
    """任何要发给前端 / 写进日志的文字，先把 Key 抹掉。"""
    if key:
        text = text.replace(key, "****")
    return re.sub(r"sk-[A-Za-z0-9]{8,}", "sk-****", text)


async def _agent_for(key: str) -> PlanexAgent:
    """每把 Key 一个 PlanexAgent（连接凭据在 AIClient 里，模型名按请求传）。"""
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    async with _AGENT_LOCK:
        agent = _AGENTS.get(h)
        if agent is None:
            cfg = ModelConfig(
                provider="openai", model=DEFAULT_MODEL, base_url=BASE_URL, api_key=key,
                reasoning_effort=REASONING_EFFORT, temperature=1.0,
            )
            agent = PlanexAgent(
                session_root=SESSION_ROOT,
                ai_client=AIClient(cfg),
                # light-skill：领域知识全部经 Agent Skill 按需载入，tools / system_prompt 都不给
                skill_sets=[retail_skill.get_skill_path()],
                runtime_config=RuntimeConfig(log_label=f"retail-{h[:6]}", recursion_limit=RECURSION_LIMIT),
                expert_config=ExpertConfig(),
            )
            await agent.ainit()
            _AGENTS[h] = agent
        return agent


# ---------------------------------------------------------------------------
# 页面 & Key 接口
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def config():
    return {"models": list(MODELS), "default_model": DEFAULT_MODEL}


class KeyBody(BaseModel):
    client_id: str
    api_key: str


@app.post("/api/key")
async def save_key(body: KeyBody):
    cid = _check_id(body.client_id, "client_id")
    key = body.api_key.strip()
    if len(key) < 8 or any(c.isspace() for c in key):
        return JSONResponse({"ok": False, "message": "API Key 格式不对"}, status_code=400)
    # 先向 DeepSeek 校验，免得存了个坏 Key，到聊天时才报错
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        return JSONResponse({"ok": False, "message": f"无法连接 DeepSeek：{type(e).__name__}"}, status_code=502)
    if r.status_code in (401, 403):
        return JSONResponse({"ok": False, "message": "API Key 无效（DeepSeek 返回 401）"}, status_code=400)
    if r.status_code >= 400:
        return JSONResponse({"ok": False, "message": f"DeepSeek 校验失败（HTTP {r.status_code}）"}, status_code=502)
    _KEYS[cid] = key
    return {"ok": True, "configured": True}


@app.get("/api/key/status")
def key_status(client_id: str):
    return {"configured": _check_id(client_id, "client_id") in _KEYS}


@app.delete("/api/key")
def delete_key(client_id: str):
    _KEYS.pop(_check_id(client_id, "client_id"), None)
    return {"ok": True, "configured": False}


# ---------------------------------------------------------------------------
# 会话工作目录
# ---------------------------------------------------------------------------
def _workdir(sid: str) -> Path:
    """会话工作目录（与 PlanexAgent 的约定一致：<session_root>/exec/<sid>）。"""
    return SESSION_ROOT / "exec" / _check_id(sid, "session_id")


def _mount_data(wd: Path) -> None:
    """首次使用时，把业务数据落到会话目录的 data/（skill 的代码只认相对路径 data/ 与 state/）。

    参考 bench/runtime/run_episode.py 的 _mount_db：skill 的能力是脚本经 execute 调的，
    独立进程，所以业务数据必须以文件形式在会话目录里；写操作由 skill 写在 state/（写时复制）。
    """
    if (wd / "data" / "orders.json").exists():
        return
    (wd / "data").mkdir(parents=True, exist_ok=True)
    for t in TABLES:
        shutil.copy2(DATA_DIR / f"{t}.json", wd / "data" / f"{t}.json")
    shutil.rmtree(wd / "state", ignore_errors=True)


def _journal(wd: Path) -> list[dict[str, Any]]:
    j = wd / "state" / "journal.jsonl"
    if not j.exists():
        return []
    out = []
    for line in j.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _safe_path(sid: str, rel: str) -> Path:
    """会话内文件路径解析。拒绝越界（含指向会话目录之外的软链接，如 skills/）。"""
    wd = _workdir(sid).resolve()
    p = (wd / rel).resolve()
    if not rel or rel.startswith("/") or wd not in p.parents or not p.is_file():
        raise HTTPException(404, "文件不存在")
    return p


# ---------------------------------------------------------------------------
# 聊天（SSE）
# ---------------------------------------------------------------------------
class ChatBody(BaseModel):
    client_id: str
    session_id: str
    message: str
    model: str | None = None
    resume: bool = False            # True：这句话是在回答上一轮 Agent 发起的澄清


def _frame_dict(frame) -> tuple[str, dict[str, Any]]:
    name = str(getattr(frame.event, "value", frame.event))
    data = frame.data.model_dump(exclude_none=True) if hasattr(frame.data, "model_dump") else {}
    if name == "tool_result" and isinstance(data.get("result"), str):
        # 帧里的 result 是一段 JSON：{"content": "...", ...}。先取出正文，再截断——
        # 直接截断 JSON 会得到一段不完整的、带转义的文本。
        try:
            j = json.loads(data["result"])
            if isinstance(j, dict) and isinstance(j.get("content"), str):
                data["result"] = j["content"]
        except (json.JSONDecodeError, TypeError):
            pass
        if len(data["result"]) > MAX_RESULT_CHARS:
            data["result"] = data["result"][:MAX_RESULT_CHARS] + "\n…（已截断）"
            data["truncated"] = True
    return name, data


@app.post("/api/chat")
async def chat(body: ChatBody, request: Request):
    cid = _check_id(body.client_id, "client_id")
    sid = _check_id(body.session_id, "session_id")
    key = _KEYS.get(cid)
    if not key:
        raise HTTPException(401, "尚未配置 API Key")
    model = body.model if body.model in MODELS else DEFAULT_MODEL
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "消息为空")

    async def gen():
        try:
            agent = await _agent_for(key)
            wd = agent.workdir(sid)
            _mount_data(wd)
            kw = {"query": "", "resume_payload": text} if body.resume else {"query": text}
            async for frame in agent.astream4ui(session_id=sid, model_name=model, **kw):
                if await request.is_disconnected():
                    return
                name, data = _frame_dict(frame)
                yield {"event": name, "data": json.dumps(data, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001 —— 任何异常都要以 error 帧告知前端，而不是断流
            msg = _scrub(f"{type(e).__name__}: {e}", key)
            log.warning("chat failed: %s", msg)
            yield {"event": "error", "data": json.dumps({"code": "500", "reason": "server", "message": msg},
                                                        ensure_ascii=False)}
        # 每轮结束附带一次写操作流水（自定义事件，前端用来刷新“数据变更”面板）
        yield {"event": "state", "data": json.dumps({"journal": _journal(_workdir(sid))}, ensure_ascii=False)}

    return EventSourceResponse(gen(), ping=15)


class ResetBody(BaseModel):
    session_id: str


@app.post("/api/reset")
def reset(body: ResetBody):
    shutil.rmtree(_workdir(body.session_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/state")
def state(session_id: str):
    return {"journal": _journal(_workdir(session_id))}


# ---------------------------------------------------------------------------
# 答案卡片引用的文件
# ---------------------------------------------------------------------------
@app.get("/api/table")
def table(session_id: str, path: str = Query(...)):
    p = _safe_path(session_id, path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]
        cols = list({k: None for r in rows if isinstance(r, dict) for k in r})
        rows = [[(r.get(c) if isinstance(r, dict) else None) for c in cols] for r in rows]
    else:
        reader = list(csv.reader(io.StringIO(text)))
        cols, rows = (reader[0], reader[1:]) if reader else ([], [])
    total = len(rows)
    return {"columns": cols, "rows": rows[:500], "total": total}


@app.get("/api/file")
def file(session_id: str, path: str = Query(...)):
    return FileResponse(_safe_path(session_id, path), headers={"Cache-Control": "no-store"})
