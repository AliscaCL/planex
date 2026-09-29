# 零售客服 Agent（前后端一体）

一个带前端的零售客服 Agent 服务：顾客用自然语言提问 → Agent 认证身份、查订单/商品、按政策办理取消/修改/退货/换货/改地址 → 前端把**完整过程**与**回复顾客的答案卡片**按 query 展示出来。

- **Agent**：`PlanexAgent`（`harness-planex`）+ `retail_skill`（Agent Skill）+ DeepSeek。组装方式取自 `planex-ie3/bench` 的 **light-skill 臂**（`bench/runtime/arms.py`）：`skill_sets=[retail_skill]`，`tools` / `system_prompt` 都不给，领域政策全部经 Skill 按需载入。
- **数据**：τ-bench retail 的 `users / orders / products`（`data/`，来自 `bench/data/cache/tau-bench`，许可见 `data/TAU_BENCH_LICENSE.txt`）。
- **前端**：仿「溯因」的对话界面——思考 / 读取技能 / 执行脚本 / 观测结果 / 回复顾客，外加 API Key 区、澄清（确认闸门）卡片、写操作流水。

## 启动 / 停止（Docker，当前部署方式）

```bash
docker compose up -d --build          # 宿主机 18100 -> 容器 8000；RETAIL_PORT=18101 可改端口
docker compose logs -f retail-agent   # 看日志
docker compose down                   # 停止并移除容器（会话数据在命名卷 retail_sessions 里，保留）
```

- 镜像 `retail-agent:local`（基础镜像 `python:3.13.11-slim`，pip 走阿里云镜像，与 `Innocellence-Gaias-Hub/aichat-api/Dockerfile.local` 的约定一致）。
- `planex-core` 与 `retail_skill` 以**预构建 wheel** 装入（`wheels/`），镜像里没有源码树。更新它们之后重新构建 wheel 再 build 镜像：
  ```bash
  G=/data/PyProject/dev/Innocellence-Gaias-Hub
  uv build --wheel -o wheels $G/harness-planex
  uv build --wheel -o wheels $G/planex-ie3/bench/skills/retail_skill
  ```
- 容器以非 root（uid 10001）运行；**不含任何密钥**，DeepSeek API Key 只在页面里输入。
- `restart: unless-stopped`，带健康检查。

### 不用 Docker 时（宿主机直接跑，开发用）

```bash
./run.sh          # 默认 http://<本机>:18100 ；用它之前先 docker compose down，避免端口冲突
./stop.sh         # 只停宿主机进程（按端口定位）
```

## API Key

- 在页面上方输入 DeepSeek API Key，点「保存」：服务端先向 DeepSeek `GET /v1/models` 校验；通过后输入框清空并显示 `****`，状态变为「已连接」。点「更换」可重新输入。
- Key **只存在服务端内存**（按浏览器的随机 `client_id` 索引）：不写盘、不进日志（异常信息会先抹掉 Key）、不进 URL、不回传前端；浏览器 DOM / localStorage 里也没有明文。服务重启后需重新输入。
- 页面刷新后仍显示 `****`（服务端记得，浏览器不记明文）。

## 页面怎么读

| 元素 | 含义 |
|---|---|
| 🧠 思考 | 模型的推理（reasoning） |
| 📖 读取文件 / 📝 写入脚本 / ⚡ 执行命令 | Agent 的行动：读 `SKILL.md`、写脚本、跑脚本（skill 的能力经 `execute` 里的 `import retail_skill` 调用） |
| 📊 观测结果 | 工具返回的真实结果 |
| 💬 回复顾客 | **答案卡片**——顾客真正看到的内容（文字 / 表格 / 图） |
| ❓ 澄清卡 | 写操作前的确认闸门（policy 要求写操作前列明细并取得明确肯定），点选项即回答 |
| 数据变更流水 | 本会话发生过的写操作（取消/修改/退货/换货/改地址/转人工） |

顶部「试试」是 τ-bench 的示例开场白（顾客口吻、英文）。「新会话」会丢弃当前会话并重置业务数据副本。

## 结构

```
Dockerfile / docker-compose.yml / wheels/   容器部署
app/server.py     FastAPI + SSE：/api/key /api/chat /api/reset /api/state /api/table /api/file
static/index.html 前端（单文件，无构建）
data/             τ-bench retail 数据（orders / products / users）
sessions/         运行时：每个会话一个工作目录 exec/<session_id>/{data,state,outputs}
```

## 数据怎么流（与 bench 一致）

skill 的能力是脚本经 `execute` 调的、每次独立进程，所以业务数据以文件形式放进会话目录：首次对话时把 `data/*.json` 拷到 `sessions/exec/<sid>/data/`；skill 的写操作走**写时复制**落在 `state/`，并追加 `state/journal.jsonl`（前端的「数据变更流水」读它）。每个会话的数据互相独立，公共数据不会被改。

## 安装（已在 `.venv` 里完成，仅供复现）

```bash
G=/data/PyProject/dev/Innocellence-Gaias-Hub
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python --index-url https://mirrors.aliyun.com/pypi/simple/ \
    -e $G/harness-planex $G/planex-ie3/bench/skills/retail_skill \
    'fastapi>=0.116' 'uvicorn[standard]>=0.30' 'sse-starlette>=2.0' httpx
```

本项目**不修改** Innocellence-Gaias-Hub 里的任何代码或它的 venv。

## 已知限制

- 会话上下文用进程内 `InMemorySaver`（没接 Postgres）：服务重启后历史丢失。
- 数据是 τ-bench 的模拟库，写操作只改会话内的副本。
- 这是 τ-bench 的英文场景，示例与 Agent 回复以英文为主；顾客用中文提问时能否命中 skill 的路由词表未做保证。
