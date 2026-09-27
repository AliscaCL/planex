"""溯因 Agent —— 基于 deepagents 的内存根因分析智能体。

工具均返回与仿真页面一致的确定性数据（无需真实主机），因此整套系统可端到端运行。
每个会话拥有独立的内存状态（kill 会真实改变可用内存读数）。
"""
from __future__ import annotations
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from deepagents import create_deep_agent

TOTAL_GIB = 62.0

# 进程清单（与仿真 / 真实排查一致）：旧版本 VS Code Server 残留可清理，业务进程保留
PROCS = [
    dict(pid=254038, user="inno", comm="node · Pylance 语言服务", rss=2023,
         server="Stable-1b50d58d（当前版本）", exe="~/.vscode-server/.../pylance/server.bundle.js",
         cwd="~/.vscode-server/extensions/ms-python.vscode-pylance", owner="Python 语言服务（活跃会话）", killable=False),
    dict(pid=14899, user="inno", comm="node · extensionHost", rss=1824,
         server="Stable-6928394f（旧版本残留）", exe="~/.vscode-server/cli/servers/Stable-6928394f.../node",
         cwd="/home/inno", owner="旧版本扩展宿主（无客户端连接）", killable=True),
    dict(pid=143479, user="inno", comm="node · extensionHost", rss=1712,
         server="Stable-6928394f（旧版本残留）", exe="~/.vscode-server/cli/servers/Stable-6928394f.../node",
         cwd="/home/inno", owner="旧版本扩展宿主（无客户端连接）", killable=True),
    dict(pid=243779, user="inno", comm="node · extensionHost", rss=1672,
         server="Stable-1b50d58d（当前版本）", exe="~/.vscode-server/cli/servers/Stable-1b50d58d.../node",
         cwd="/home/inno", owner="当前 VS Code 扩展宿主（活跃）", killable=False),
    dict(pid=121464, user="inno", comm="node · extensionHost", rss=1541,
         server="Stable-6928394f（旧版本残留）", exe="~/.vscode-server/cli/servers/Stable-6928394f.../node",
         cwd="/home/inno", owner="旧版本扩展宿主（无客户端连接）", killable=True),
    dict(pid=199596, user="inno", comm="java · maven spring-boot", rss=928,
         server="监听 :8088", exe="redhat.java/jre/.../bin/java",
         cwd="/data/PyProject/Henry/DLT/backend/participantservice", owner="DLT 后端业务服务（运行中）", killable=False),
    dict(pid=2757, user="root", comm="whisper-asr-webservice", rss=929,
         server="监听 :9000", exe="/app/.venv/bin/python", cwd="/app", owner="语音转写 ASR 服务", killable=False),
    dict(pid=92631, user="inno", comm="node · 扩展子进程", rss=490,
         server="Stable-6928394f（旧版本残留）", exe="~/.vscode-server/.../bootstrap-fork",
         cwd="/home/inno", owner="旧版本宿主拉起的孤立子进程", killable=True),
    dict(pid=147192, user="inno", comm="node · ng serve", rss=631,
         server="监听 :4200", exe="/data/.cursor-server/.../node",
         cwd="/data/PyProject/darrick/client/angular2.0/client", owner="前端联调 dev server（运行中）", killable=False),
]

SYSTEM_PROMPT = """你是“溯因 Agent”，一个内存根因分析智能体，运行在主机 tcaiinnovation 上。
你的职责：当收到内存告警时，自主排查内存被谁占用、区分业务进程与可清理进程、定位根因，并在用户逐步确认后清理。

可用工具：
- get_memory_status：查当前内存使用率与构成
- list_top_processes：列出占内存最多的进程
- inspect_process(pid)：查某进程的启动位置、打开文件与业务归属
- classify_processes：把进程分为“可清理”与“需保留”
- kill_process(pid)：结束进程（仅允许清理已确认的旧版本残留；业务进程会被拒绝）

工作流程：
1) 先 get_memory_status 确认使用率；2) list_top_processes 看构成；3) classify_processes 分类，必要时 inspect_process 佐证；
4) 用简洁中文给出“根因结论”（通常是旧版本 VS Code Server 残留的扩展宿主在后台空转），并列出建议清理的进程，请用户确认；
5) 用户确认后再逐个 kill_process，并报告最新内存。务必保护有业务/活跃会话的进程，绝不擅自清理。

要求：直接调用上述业务工具并用中文作答；不要创建待办清单（write_todos），不要使用文件读写等通用工具。回答简洁、专业、像资深运维。"""


def make_agent(model):
    """每个会话独立的内存状态 + deepagents 智能体。"""
    state = {"used": 51.03, "killed": set()}

    def pct():
        return state["used"] / TOTAL_GIB * 100

    @tool
    def get_memory_status() -> str:
        """查询当前主机的内存使用情况：使用率、总量、已用、可用与 Swap。"""
        return (f"内存使用率 {pct():.1f}%，总 {TOTAL_GIB:.0f} GiB，已用 {state['used']:.1f} GiB，"
                f"可用 {TOTAL_GIB - state['used']:.1f} GiB，Swap 0B（无交换分区，存在 OOM 风险）。"
                f"{'已超过 80% 告警阈值。' if pct() >= 80 else '已回落到阈值以下。'}")

    @tool
    def list_top_processes() -> str:
        """列出按常驻内存(RSS)降序排列的 TOP 进程。"""
        rows = [f"{p['pid']:>6} | {p['user']:<4} | {p['rss']:>4} MiB | {p['comm']} | 来源 {p['server']}"
                for p in PROCS if p["pid"] not in state["killed"]]
        return "PID | 用户 | RSS | 进程 | 来源\n" + "\n".join(rows)

    @tool
    def inspect_process(pid: int) -> str:
        """查看指定进程的启动位置、工作目录、来源与业务归属，判断是否可清理。"""
        p = next((x for x in PROCS if x["pid"] == pid), None)
        if not p:
            return f"未找到 PID {pid}（可能已退出）。"
        if pid in state["killed"]:
            return f"PID {pid} 已被清理。"
        return (f"PID {pid}｜{p['comm']}\n启动位置(exe)：{p['exe']}\n工作目录(cwd)：{p['cwd']}\n"
                f"来源：{p['server']}\n业务归属：{p['owner']}\n"
                f"建议：{'可清理（旧版本残留、无客户端连接）' if p['killable'] else '保留（业务服务或活跃会话）'}")

    @tool
    def classify_processes() -> str:
        """对 TOP 进程做业务归属分类，区分“可清理”与“需保留”，并给出可释放内存估算。"""
        kill = [p for p in PROCS if p["killable"] and p["pid"] not in state["killed"]]
        keep = [p for p in PROCS if not p["killable"]]
        freeable = sum(p["rss"] for p in kill) / 1024
        s = ["【可清理】旧版本 VS Code Server（Stable-6928394f）残留、无客户端连接："]
        s += [f"  - PID {p['pid']} {p['comm']} {p['rss']} MiB" for p in kill]
        s.append(f"  合计可释放约 {freeable:.2f} GiB")
        s.append("【需保留】有业务或活跃会话，绝不清理：")
        s += [f"  - PID {p['pid']} {p['comm']}（{p['owner']}）" for p in keep]
        return "\n".join(s)

    @tool
    def kill_process(pid: int) -> str:
        """结束指定 PID 的进程。仅允许清理已确认的旧版本残留进程；业务/活跃会话进程会被拒绝。"""
        p = next((x for x in PROCS if x["pid"] == pid), None)
        if not p:
            return f"未找到 PID {pid}。"
        if pid in state["killed"]:
            return f"PID {pid} 已经被清理过了。"
        if not p["killable"]:
            return (f"⚠ 拒绝清理 PID {pid}（{p['comm']}）：该进程属于「{p['owner']}」，"
                    f"是有明确业务需求/活跃会话的进程，不能清理。")
        state["killed"].add(pid)
        state["used"] = max(0.0, state["used"] - p["rss"] / 1024)
        return (f"已对 PID {pid}（{p['comm']}）执行 kill -TERM，释放约 {p['rss']/1024:.2f} GiB。"
                f"最新内存使用率 {pct():.1f}%（已用 {state['used']:.1f} / {TOTAL_GIB:.0f} GiB）。"
                f"{'✓ 已回落到 80% 阈值以下。' if pct() < 80 else ''}")

    tools = [get_memory_status, list_top_processes, inspect_process, classify_processes, kill_process]
    agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT,
                              checkpointer=InMemorySaver())
    # 供后端识别“需要在 UI 上展示”的业务工具
    agent._display_tools = {t.name for t in tools}
    return agent, state
