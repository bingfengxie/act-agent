# act-agent

独立运行的多租户活动管理 Agent：LangGraph 编排 + Streamable HTTP MCP + Claude 风格 Skills。
不依赖 Claude Code 常驻，任何 HTTP 客户端都能完成「一次性创建大活动 + 多个商品活动」这类任务。

## 能力

- **组合创建 / 编辑 / 删除活动**：复用 `../act-skills` 下 4 个 skill（含 HITL 状态机）
- **多租户**：tenant 隔离到会话、checkpoint、MCP 凭据、审计
- **分布式会话**：LangGraph checkpoint 落 Postgres，任意 worker 都能恢复并继续
- **上下文压缩（破坏性）**：更早的对话折进滚动摘要后从 state 删除，`messages` 有界；结构化 `task_state` 永不压缩，未完成活动不会丢
- **强制 HITL**：写操作由运行时 `interrupt()` 弹出预览，用户跨轮确认后才执行
- **认证与权限**：JWT + 角色（viewer/operator/admin），工具级白名单 + 审计
- **轻量切面层（middleware）**：审计与 PII 脱敏作为 I/O 边界切面挂载；HITL 与摘要保持图级实现
- **全方位审计**：工具调用（读/写/本地）+ 用户原始输入 + 最终结果，落库前 PII 脱敏

## 架构

```
Streamable HTTP (stateless, SSE 响应体) (JWT)
   │
FastAPI ── auth ── RBAC ── sessions ── chat(Streamable HTTP)
   │
LangGraph thread = tenant:user:conversation
   ├─ route    选 skill（关键词 → LLM 兜底）
   ├─ collect  抽取已收集参数 → task_state.draft（新建类任务）
   ├─ compact  破坏性压缩（摘要 + 删除已摘要前缀）
   ├─ agent    LLM + MCP 工具 + skill 指令
   └─ tools    权限 → interrupt 预览 → 执行 → 审计
        └─ tools → compact → agent（每次模型调用前都重新结算预算）
   │
   ├── Postgres: checkpoint / sessions / tenants / users / audit
   ├── act-skills（只读，渐进加载）
   └── act-mcp（streamable_http，每租户独立 client）
```

## 快速开始

前置：MCP 服务已启动（默认 `http://localhost:8090/mcp`）。

```bash
cp .env.example .env          # 按需修改 LLM 与数据库
uv sync --python 3.12
# 国内网络慢时可换镜像：
# uv sync --python 3.12 --default-index https://mirrors.aliyun.com/pypi/simple/

# 单机模式（SQLite + InMemorySaver），可直接跑通
uv run uvicorn act_agent.main:app --port 8088
```

首次启动会自动建库并写入演示账号（tenant=`default`）：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| admin | admin123 | admin |
| operator | operator123 | operator |
| viewer | viewer123 | viewer |

检查：

```bash
curl -s localhost:8088/health | python3 -m json.tool
uv run python scripts/smoke_mcp.py        # 列出 MCP 工具
```

生产模式（Postgres checkpoint + 跨实例锁）：

```bash
make db-up                                 # docker compose 起 postgres
# .env 中设置：
#   ACT_AGENT_DATABASE_URL=postgresql+asyncpg://act:act@localhost:5432/act_agent
#   ACT_AGENT_CHECKPOINT_DSN=postgresql://act:act@localhost:5432/act_agent
#   ACT_AGENT_LOCK_BACKEND=pg
uv run uvicorn act_agent.main:app --port 8088 --workers 2
```

## 使用

### CLI（最省事）

```bash
uv run python -m act_agent.cli --username operator --password operator123
```

输入层使用 `prompt_toolkit`：中文退格/光标正常（macOS 原生 `input()` 基于 libedit，中文宽字符会删不干净）；
多行内容可直接粘贴，**Enter 发送，Option/Alt+Enter 换行**；非交互管道输入仍走兼容路径。

### HTTP + Streamable HTTP

每个用户消息是一个自包含 POST：响应体为 SSE 事件格式，turn 结束即关闭连接，不维护跨请求的
服务端会话，LB 可把同 session 的不同消息路由到任意 worker（状态由 Postgres checkpoint +
advisory lock 恢复）。

```bash
# 1. 登录
TOKEN=$(curl -s localhost:8088/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"tenant":"default","username":"operator","password":"operator123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2. 建会话
SID=$(curl -s localhost:8088/v1/sessions -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 3. 发消息（Streamable HTTP，SSE 响应体）
curl -N localhost:8088/v1/sessions/$SID/messages -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"一次性创建活动：名称中秋节3，折扣，2026-8-10 到 2026-8-15，全部人群，预算1212元。商品1 门店10001 SKU1111 UPC xxxx 折扣0.7；商品2 门店10002 SKU2222 UPC yyyy 折扣0.7"}'
```

SSE 事件：

| event | data |
| --- | --- |
| `start` | `{session_id, thread_id}` |
| `token` | `{text}` 模型增量输出 |
| `message` | `{content}` 完整回复（无 token 流时） |
| `interrupt` | `{payload}` 预览卡片 + `confirm_prompt` |
| `error` | `{message}` |
| `done` | `{status: idle \| awaiting_confirmation \| failed}` |

收到 `interrupt` 后，用 `/resume` 提交用户回复：

```bash
curl -N localhost:8088/v1/sessions/$SID/resume -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"answer":"确认创建 中秋节3"}'
```

### 时间补全（运行时确定性规则）

用户只给日期时（`2026-8-15`），模型可以直接传原值：运行时会在预览与执行前统一补全为

- `startTime` → `YYYY-MM-DDT00:00`
- `endTime` → `YYYY-MM-DDT23:59`

补全后的最终值会出现在预览卡片里，用户确认的就是实际写入的值。

### HITL 语义

预览与实际写入**不可能同轮发生**：预览由运行时 `interrupt()` 产生并中断图，确认由确定性解析器判定。

| 用户回复 | 判定 |
| --- | --- |
| `确认创建 中秋节3`、`确认`、`yes`、`ok` | 执行 |
| `取消`、`no`、`不要了` | 取消，不写库 |
| `好`、`可以`、`行`、`y`、`嗯` | 含糊 → 再问一次；仍含糊则取消 |

模型无法绕过：即使它直接调用写工具，也一定会先被 interrupt 拦下；权限不足时工具根本不会出现在它的可用列表里。

### 会话状态 / 审计

```bash
curl -s localhost:8088/v1/sessions/$SID/state -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -s "localhost:8088/v1/admin/audit?limit=20" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

审计表全方位留痕，按 `event_type` 区分：

| event_type | 内容 | 落点 |
| --- | --- | --- |
| `tool_call` | 工具调用（读/写/本地，含 deny/cancel/failed） | `tool_name` + `action`(create/update/delete/read) + `args` |
| `user_input` | 用户原始输入（message 或 resume 回答） | `content` |
| `assistant_output` | 最终返回给用户的结果 | `content` |

所有落库内容先做 PII 脱敏（手机号/身份证/邮箱/银行卡），审计表不存明文敏感信息。
`audit:read` 权限仅 admin 拥有。

## 多租户

- 会话主键 `thread_id = "{tenant_id}:{user_id}:{session_id}"`，checkpoint 天然隔离
- JWT 内含 `tenant`，所有查询强制按租户过滤；请求体无法指定租户
- 租户可在 `tenants.config` 覆盖 MCP 服务、技能开关、模型等
- 新建租户：

```bash
uv run python scripts/provision_tenant.py --tenant acme --username root --password secret123
```

## 上下文压缩（破坏性）

触发：估算 token 超过 `MAX_CONTEXT_TOKENS * COMPRESS_RATIO`，或消息数超过 `KEEP_RECENT_MESSAGES * 2`。

- 更早消息交给 LLM 生成**增量摘要**（summary），随后用 `RemoveMessage(REMOVE_ALL_MESSAGES)`
  把「摘要 + 保留的尾巴」写回 state——**已摘要的消息从 state 中真正删除**
- 因此 `messages` 有界（≈ 最近 N 条 + 当前轮），checkpoint 写入量不再随会话长度二次增长；
  旧 checkpoint 版本仍保留历史，审计另有独立审计表
- `compact` 同时挂在 `collect → agent` 入口和 `tools → agent` 回路上：轮内工具结果膨胀、
  HITL resume 路径都会在每次模型调用前重新结算预算
- 压缩时同步回退 `task_state.collected_upto`（它也是 `messages` 下标），避免漏抽取
- 绝不压掉当前轮的 `HumanMessage`；若当前轮自身超窗，`fit_view` 只在模型视图里截断超长
  工具结果，不改 state
- `task_state` 结构化保存进行中的活动草稿、缺失字段、修正记录、HITL 阶段，**每轮全量注入**，
  因此 40 轮后仍能准确续建同一活动
- 摘要失败时降级为「旧摘要 + 截断片段」，不会打断对话

## Skills 与 MCP

- 启动时扫描 `../act-skills/*/SKILL.md`，解析 frontmatter
- 路由：关键词规则优先，未命中再让 LLM 在 description 中选择；确认类短回复不会触发重新路由
- 渐进加载：只注入 SKILL.md 正文；`skill_read_reference` / `skill_run_script` 按需读取参考文档、执行校验脚本（`python3 -I`，白名单 + 30s 超时 + 输出截断）
- MCP 配置兼容 Claude 格式（`config/mcp.json`）；每租户独立 client，缓存复用
- 工具按角色过滤后注入，模型看不到无权限工具

## 配置

全部通过环境变量（前缀 `ACT_AGENT_`），见 `.env.example`。常用：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `ACT_AGENT_LLM_PROVIDER` | `openai` / `anthropic` | openai |
| `ACT_AGENT_LLM_MODEL` | 模型名 | deepseek-flash |
| `ACT_AGENT_LLM_BASE_URL` | OpenAI 兼容地址 | https://api.deepseek.com |
| `ACT_AGENT_LLM_API_KEY` | 未设置时回退 `DEEPSEEK_API_KEY`/`OPENAI_API_KEY` | - |
| `ACT_AGENT_CHECKPOINT_DSN` | 留空 = InMemorySaver（仅开发） | - |
| `ACT_AGENT_LOCK_BACKEND` | `pg` / `memory` | memory |
| `ACT_AGENT_SKILLS_DIR` | skills 目录 | `../act-skills` |
| `ACT_AGENT_MCP_CONFIG_PATH` | MCP 配置 | `config/mcp.json` |
| `ACT_AGENT_MAX_CONTEXT_TOKENS` | 上下文预算 | 64000 |
| `ACT_AGENT_KEEP_RECENT_MESSAGES` | 保留最近条数 | 20 |

## Docker

```bash
docker compose up -d --build
```

容器内访问宿主机 MCP 需把 `config/mcp.json` 的 url 改为 `http://host.docker.internal:8090/mcp`。

## 测试

```bash
uv run pytest -q
uv run ruff check src tests
```

- `tests/unit`：HITL 解析、权限矩阵、时间补全、压缩、skill 路由与脚本执行、MCP 配置转换、JWT
- `tests/unit/test_pii.py`：PII 脱敏规则（手机号/身份证/邮箱/银行卡、递归脱敏、切面方法）
- `tests/unit/test_audit.py`：审计切面（事件类型映射、脱敏落库、deny/cancel、完整链路顺序、切面链组装）
- `tests/integration/test_graph_flow.py`：脚本化 LLM + 假工具的完整图流程
  （预览 → 含糊追问 → 确认 → 执行 → 审计；取消、越权、只给日期的分支）
- `tests/integration/test_api_auth.py`：登录、角色权限、会话隔离
- `tests/integration/test_mcp_live.py`：真实 act-mcp 工具发现（不可达自动跳过）
- `tests/integration/test_pg_checkpoint.py`：两个独立 checkpointer 实例共享 Postgres，
  验证跨进程恢复与 `resume`（无 Postgres 自动跳过）

已实测通过的完整分布式链路：进程 A 发起创建 → 预览中断 → 重启服务（进程 B）→
从 checkpoint 恢复待确认状态 → 确认/取消 → 审计落库。

## 目录

```
src/act_agent/
├── main.py / runtime.py / service.py / cli.py / llm.py
├── api/         auth, sessions, chat(Streamable HTTP), admin(audit)
├── auth/        jwt, passwords, rbac
├── graph/       builder, state, nodes, prompts, compress, hitl, preview, context
├── middleware/  base, audit, pii（轻量切面层）
├── skills/      registry, router, tools
├── mcp/         config, client
├── storage/     models, db, repository, checkpointer, locks, bootstrap
└── utils/       tokens
```

## 已知限制

- 增量 `token` 流不做 PII 脱敏（流式逐 token 无法可靠识别跨 token 的敏感串）；审计与最终 `message` 事件已脱敏
- 压缩摘要质量取决于所选模型；关键参数另有 `task_state` 兜底
- 租户级模型覆盖尚未实现（租户可覆盖 MCP 服务）
- 单机 `InMemorySaver` 模式下重启会丢失会话状态，仅用于开发
- 生产务必设置 `ACT_AGENT_JWT_SECRET`（≥32 字符），默认值仅供开发
- 默认开启的 `LANGCHAIN_TRACING_V2` 会把对话上报到 LangSmith；本项目 `.env.example` 已显式关闭
