# 开发指南

本文档描述 AI_Question 当前主干上的工作流、状态数据、LLM 客户端、LaTeX 后处理和配置项。内容以维护者开发为目标，避免记录已经移除的历史接口。

## 工作流

```mermaid
flowchart TD
    START([开始]) --> PLAN[命题规划]
    PLAN --> PGEN[命题 Agent]
    PGEN --> SGEN[解题 Agent]
    SGEN --> REVIEW{并行审核}
    REVIEW --> MATH[数学审核]
    REVIEW --> PHYS[物理审核]
    REVIEW --> STRUCT[结构审核]
    REVIEW --> QUALITY[质量审核]
    MATH --> ARB[仲裁 Agent]
    PHYS --> ARB
    STRUCT --> ARB
    QUALITY --> ARB
    ARB -->|PASS| ISO[公式与图片隔离]
    ARB -->|PASS + style| ISO
    ARB -->|RETRY_PROBLEM| PGEN
    ARB -->|RETRY_SOLUTION| SGEN
    ARB -->|ABORT 或超限| STOP([终止])
    ISO --> FMT[LaTeX 排版]
    FMT --> MERGE[占位符回填]
    MERGE --> TFIX[模板修正]
    TFIX --> DONE([输出])
```

核心入口是 `engine.state_machine.GenerationStateMachine`。状态机按以下阶段运行：

```text
INIT -> PLANNING -> PROBLEM_GENERATING -> SOLUTION_GENERATING
     -> REVIEWING -> ARBITRATING
     -> FORMATTING -> TEMPLATE_FIXING -> DONE
```

任一阶段抛出异常时，状态机进入 `ERROR`。仲裁返回 `ABORT` 时，状态机进入 `ABORTED`。

## 节点职责

| 节点 | 实现位置 | 职责 |
| --- | --- | --- |
| 命题规划 | `spec.planner` | 根据输入主题、难度和总分生成规划笔记 |
| 命题 Agent | `agents.problem_generator` | 生成题干、标题和小问结构 |
| 解题 Agent | `agents.solution_generator` | 生成参考答案、评分点和可选图片说明 |
| 数学审核 | `agents.reviewers` | 检查数学推导、符号定义和计算一致性 |
| 物理审核 | `agents.reviewers` | 检查物理模型、边界条件和量纲一致性 |
| 结构审核 | `agents.reviewers` | 通过规则检查编号、分值和公式标签 |
| 质量审核 | `agents.reviewers` | 检查 CPHOS 联考试题深度、梯度和可评分性 |
| 仲裁 Agent | `agents.arbiter` | 汇总审核结果，输出结构化裁决 |
| 隔离器 | `latex.isolate` | 提取标题、公式和图片占位符 |
| 格式化 Agent | `latex.format` | 在不接触原始公式的前提下生成 CPHOS LaTeX 结构 |
| 回填器 | `latex.merge` | 回填公式、图片、引用、小问命令和总分 |
| 模板修正 | `latex.template_agent` | 检查并修复文档类、环境闭合和必要命令 |

## 仲裁语义

仲裁输出由 `model.schema.ArbiterDecision` 校验，字段为：

| 字段 | 合法值 |
| --- | --- |
| `decision` | `PASS`、`RETRY_PROBLEM`、`RETRY_SOLUTION`、`ABORT` |
| `error_category` | `none`、`style`、`fatal` |

合法组合如下：

| 组合 | 路由 |
| --- | --- |
| `PASS + none` | 进入 LaTeX 后处理 |
| `PASS + style` | 映射为 `PASS_WITH_EDITS` 后进入 LaTeX 后处理 |
| `RETRY_PROBLEM + fatal` | 回到命题 Agent，并递增 `problem_retry_count` |
| `RETRY_SOLUTION + fatal` | 回到解题 Agent，并递增 `solution_retry_count` |
| `ABORT + fatal` | 终止当前流程 |

其他组合会在 schema 层被拒绝。路由层仍保留同样的防御校验，用于处理运行期状态被外部代码修改或旧状态恢复的场景。

当仲裁模型返回非法结构化字段时，`agents.arbiter` 会把非法载荷和校验错误追加给仲裁模型，要求其重新调用 `arbiter_decision` 工具。二次校验仍失败时，流程返回 `ABORT + fatal`。

## 重试计数

系统使用分阶段重试计数：

| 字段 | 含义 |
| --- | --- |
| `problem_retry_count` | `RETRY_PROBLEM` 的累计次数 |
| `solution_retry_count` | `RETRY_SOLUTION` 的累计次数 |
| `retry_count` | 两个阶段重试次数之和 |

单阶段达到 `MAX_RETRY_COUNT` 时终止当前题目；总重试次数达到 `2 * MAX_RETRY_COUNT` 时也终止当前题目。`PASS` 和 `ABORT` 不增加重试计数。

## 状态数据

`model.state.WorkflowData` 是状态机内部流转字典的类型视图，由多个阶段记录合并而成：

| 类型 | 写入来源 | 字段 |
| --- | --- | --- |
| `TaskInput` | `spec.normalizer`、`spec.planner` | `mode`、`topic`、`source_material`、`difficulty`、`total_score`、`difficulty_profile`、`planning_notes` |
| `GenerationOutput` | `agents.problem_generator`、`agents.solution_generator` | `title`、`problem_text`、`solution_text`、`draft_content` |
| `ReviewOutput` | `agents.reviewers` | `math_review`、`physics_review`、`structure_review`、`quality_review` |
| `ArbitrationOutput` | `agents.arbiter` | `arbiter_decision`、`arbiter_feedback`、`arbiter_reason`、`error_category`、`retry_count`、`problem_retry_count`、`solution_retry_count` |
| `LaTeXOutput` | `latex.*` | `formula_dict`、`inline_dict`、`figure_dict`、`tagged_text`、`formatted_text`、`final_latex`、`template_report`、`figure_descriptions` |

各 Agent 的局部返回使用 `PlanningOutput`、`GenerationPatch`、`ReviewPatch`、`LaTeXPatch` 等 Patch 类型。新增字段时应先确定字段归属阶段，再加入对应阶段类型。

`spec.normalizer` 在创建初始状态后会检查关键字段完整性，避免后续节点读到缺失字段。

## LLM 客户端

`client.base` 提供 `BaseLLMClient`、`register_provider()` 和 provider 查询函数。当前内置 provider：

| Provider | 实现 |
| --- | --- |
| `openrouter` | `client.openrouter.OpenRouterClient` |
| `openai_compatible` | `client.openai_compat.OpenAICompatibleClient` |

新增 provider 时，定义 `BaseLLMClient` 子类并使用 `@register_provider("name")` 注册，再在 `client.__init__` 中导入该模块以触发注册。

## 后端 API

`src/api` 提供基于 FastAPI 的多用户后端，是系统唯一的对外入口；任务执行统一通过
`app.runner.execute_task` 完成。

| 模块 | 职责 |
| --- | --- |
| `api.db` | SQLite 连接（单例 + 写锁）与建表 |
| `api.store` | 用户 / token / 任务仓储；token 仅存 SHA-256 哈希 |
| `api.auth` | Bearer token 鉴权依赖（`require_user` / `require_admin`） |
| `api.jobs` | 线程池任务执行器，受 `MAX_CONCURRENT_JOBS` 限制；产物写入 `OUTPUT_DIR/{user_id}` |
| `api.progress` | 把状态机各阶段产出格式化为面向用户的结构化进度快照 |
| `api.schemas` | 请求 / 响应 pydantic 模型（OpenAPI 文档数据源） |
| `api.routes_tasks` / `api.routes_admin` | 用户任务与管理路由 |
| `api.app` | 应用工厂、lifespan（建库 / 引导 admin / 启停执行器）、`run()` 入口 |
| `api.docs_export` | 导出 OpenAPI 规范与 ReDoc 页面到 `docs/api` |

要点：

- **鉴权与角色**：token 为随机不透明串，明文仅创建时返回一次；角色分 `user` / `admin`，
  管理员拥有全权限（可访问 / 下载 / 删除任意用户的任务与产物）。首个管理员通过
  `ADMIN_BOOTSTRAP_TOKEN` 引导。
- **异步任务**：`POST /api/tasks` 提交后立即返回 `task_id`，任务在线程池后台执行，
  客户端轮询 `GET /api/tasks/{id}` 获取状态与摘要，再下载产物。
- **节点级进度**：状态机通过 `on_phase(phase, status, data)` 回调上报阶段事件
  （`running` / `completed`），由 `api.jobs` 落库到 `task_events` 表，并更新
  `tasks.phase`。客户端轮询 `GET /api/tasks/{id}/progress` 获取阶段时间线；
  每个 `completed` 事件携带由 `api.progress` 按阶段类型格式化的**结构化产出快照**
  （而非模型原始输出），长文本会截断并标注。回调链路两层容错（状态机 `_emit`
  与 jobs sink 均吞异常），进度上报失败绝不影响生成主流程。
- **产物隔离**：产物按 `OUTPUT_DIR/{user_id}/{task_id}_*` 落盘；下载端点做归属校验
  与目录穿越防护。
- **协作式取消**：`POST /api/tasks/{id}/cancel` 不强杀线程。`api.jobs` 为每个任务持有
  一个 `threading.Event` 与运行中的 `Future`：排队未启动的任务直接 `future.cancel()`
  并落 `aborted`；运行中的任务置 `aborting` 并 set event，状态机在每个**阶段边界**
  检查 `should_cancel()`，命中则抛 `TaskCancelled`，由 runner 捕获后落 `aborted`。
- **实时进度（SSE）**：`GET /api/tasks/{id}/events` 以 `text/event-stream` 推送阶段
  事件（`event: phase`）与终态（`event: status`）。实现为对 `task_events` 表的异步
  轮询生成器（间隔 `_SSE_POLL_INTERVAL`，上限 `_SSE_MAX_DURATION`），任务进入终态后
  发送 `status` 事件并关闭连接；是轮询 `/progress` 的低延迟替代。
- **跨域**：默认不启用 CORS（推荐 nginx 同源反代）。设置 `CORS_ALLOW_ORIGINS`
  （逗号分隔来源）后 `api.app` 挂载 `CORSMiddleware`。
- **文档自动生成**：运行时 `/docs`、`/redoc`、`/openapi.json` 实时暴露规范；
  `uv run physics-api-docs` 把同一份规范固化到 `docs/api`。

## 运行统计与并发隔离

`model.stats` 使用 `contextvars.ContextVar` 持有每个任务独立的统计字典，而非进程级
全局单例。`record / get_all / get_total_tokens / clear` 的签名保持不变，各 Agent 无需
改动；`run_context()` 上下文管理器在任务边界绑定独立统计字典，`app.runner.execute_task`
在每次执行时进入该上下文，因此 API 后端可并发执行多个任务而统计互不串扰。

由于 `ThreadPoolExecutor` 默认不向工作线程传播 `ContextVar`，需要在工作线程内写入
统计的代码（如 `agents.reviewers.run_reviews` 的并行审核）使用
`contextvars.copy_context().run(fn, *args)` 提交任务。

## 提示词

提示词存放在 `src/prompts/*.yaml` 中，通过 `prompts.load(agent, key, **kwargs)` 读取。变量替换仅替换显式传入的 `{key}`，不会处理未传入的花括号，因此 LaTeX 花括号可以保留在提示词模板中。

## LaTeX 后处理

后处理分为四步：

1. `latex.isolate` 提取 `<block_math>`、`$...$`、`<figure>` 和标题，替换为占位符。
2. `latex.format` 把带占位符的文本排版为 CPHOS LaTeX 文档。
3. `latex.merge` 回填公式、图片占位、引用和小问命令。
4. `latex.template_agent` 修正文档类、环境闭合和必要模板命令。

占位符完整性由 `latex.format` 和 `latex.merge` 检查。格式化 Agent 不应看到原始公式内容。

## CPHOS 模板约定

| 内容 | 约定 |
| --- | --- |
| 文档类 | `\documentclass[answer]{cphos}` |
| 题目环境 | `\begin{problem}[总分]{标题}` |
| 公式编号 | `\eqtag{N}` 或 `\eqtagscore{N}{score}` |
| 公式标签 | `\label{eq:N}` |
| Part 题干 | `\pmark{A}\label{part:A}` |
| 一级小问 | `\subq{1}\label{q:1}` |
| 二级小问 | `\subsubq{1.1}\label{q:1.1}` |
| 三级小问 | `\subsubsubq{1.1.1}\label{q:1.1.1}` |
| 解答评分 | `\solsubq{1}{分值}` 等对应命令 |
| 评分区 | `\scoring` |

图片输出为真实 `figure` 环境，并在 `figure_descriptions` 中导出绘图需求。输出层会为每张图片生成 `figN.tex` 草稿文件；启用 `AUTO_COMPILE_FIGURES` 且本地 LaTeX 环境可用时，同目录生成 `figN.pdf` 并由最终题面引用。若插图 PDF 未生成，最终题面会写入可编译的文字占位，避免引用不存在的外部文件。

## 配置项

配置分两层：

1. **可持久化设置（数据库）**：模型与服务商选择及其参数、流程开关等业务可调项，
   存于 SQLite（`llm_providers` / `model_configs` / `agent_bindings` / `app_settings`
   表），运行时只读数据库记录，仅通过管理员 API（`/api/admin/llm/*`）修改。模型配置
   与 Agent 绑定**解耦**：一条「模型配置」描述「服务商 + 模型 + 采样参数」，每个 Agent
   角色再各自绑定到某条模型配置，因此不同 Agent 可用不同模型甚至不同服务商。
2. **部署 / 基础设施 / 机密（`.env`）**：始终通过环境变量管理。

### 种子默认值（仅首次初始化时播种）

下列变量**只在数据库首次初始化时**作为初始记录写入（`SEED_*`）；之后修改这些变量
不再生效，请改用管理员 API。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `LLM_PROVIDER` | 默认服务商类型（`openrouter` / `openai_compatible`） | `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter API key（机密，入库存储；API 响应脱敏） | 空 |
| `LLM_API_KEY` | OpenAI 兼容接口 API key（机密，同上） | 空 |
| `LLM_BASE_URL` | OpenAI 兼容接口 base URL | 空 |
| `BIG_MODEL_NAME` | 命题、解题、审核、仲裁使用的模型 | 空 |
| `SMALL_MODEL_NAME` | LaTeX 格式化使用的模型 | 空 |
| `BIG_MODEL_TEMPERATURE` | 大模型温度 | `0.7` |
| `BIG_MODEL_MAX_TOKENS` | 大模型最大输出 token | `32768` |
| `ARBITER_MAX_TOKENS` | 仲裁最大输出 token | `4096` |
| `REVIEW_TEMPERATURE` | 审核 / 仲裁类 Agent 温度（确定性审核） | `0.0` |
| `SMALL_MODEL_TEMPERATURE` | 小模型温度 | `0.0` |
| `SMALL_MODEL_MAX_TOKENS` | 小模型最大输出 token | `8192` |
| `MODEL_TIMEOUT` | SDK 请求超时时间，单位秒 | `600` |
| `LLM_MAX_RETRIES` | SDK 请求最大重试次数 | `3` |
| `LLM_STREAMING` | 是否使用流式模型响应；OpenRouter 长任务默认关闭以降低空闲超时风险 | `false` |
| `MAX_RETRY_COUNT` | 单阶段最大重试次数 | `3` |
| `SOURCE_MATERIAL_MAX_CHARS` | 文献、PDF、网页导入后的源材料截断上限 | `60000` |
| `AUTO_COMPILE_FIGURES` | 是否自动把生成的 `figN.tex` 编译为 `figN.pdf` | `true` |
| `AUTO_COMPILE_LATEX` | 是否自动编译最终题目 LaTeX；CI 或无模板环境可关闭 | `false` |
| `LATEX_COMPILE_TIMEOUT` | 单次 LaTeX / TikZ 编译子进程超时（秒） | `180` |
| `LATEX_COMPILER_BACKEND` | LaTeX 编译后端：`local`（本机 subprocess）或 `remote`（独立编译服务，见 `docs/latex-service-protocol.md`） | `local` |
| `LATEX_SERVICE_BASE_URL` | 远程编译服务根 URL（`LATEX_COMPILER_BACKEND=remote` 时必填） | （空） |
| `LATEX_SERVICE_POLL_INTERVAL` | 远程编译作业状态轮询间隔（秒） | `2.0` |
| `LATEX_SERVICE_MAX_WAIT` | 远程编译作业等待终态的客户端总截止（秒） | `1200.0` |
| `SSE_POLL_INTERVAL` | SSE 进度推送轮询间隔，单位秒 | `1.0` |
| `SSE_MAX_DURATION` | SSE 连接最长存活时间，单位秒 | `1800.0` |

种子写入后，可通过以下管理端点查看 / 修改（均需管理员 token）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/admin/llm/providers` | 列出 / 创建服务商凭据 |
| GET/PATCH/DELETE | `/api/admin/llm/providers/{id}` | 查看 / 更新 / 删除服务商 |
| GET/POST | `/api/admin/llm/models` | 列出 / 创建模型配置 |
| GET/PATCH/DELETE | `/api/admin/llm/models/{id}` | 查看 / 更新 / 删除模型配置 |
| GET | `/api/admin/llm/agents` | 列出 Agent → 模型配置绑定 |
| PUT | `/api/admin/llm/agents/{role}` | 绑定某 Agent 到模型配置 |
| GET/PATCH | `/api/admin/llm/settings` | 查看 / 更新运行期应用设置 |

### 部署 / 基础设施（始终通过 `.env`）

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `LATEX_ENGINE` | 本地 LaTeX 编译命令 | `xelatex` |
| `CPHOS_TEMPLATE_DIR` | `cphos.cls` 与配套样式所在目录；相对路径按项目根目录解析 | `../CPHOS-Latex/theory` |
| `OUTPUT_DIR` | 输出目录 | `output` |
| `API_HOST` | API 服务监听地址 | `0.0.0.0` |
| `API_PORT` | API 服务端口 | `8000` |
| `MAX_CONCURRENT_JOBS` | 同时执行的生成任务上限 | `1` |
| `DB_PATH` | 用户 / token / 任务元数据 + LLM 设置的 SQLite 路径（相对按项目根解析） | `data/api.db` |
| `ADMIN_BOOTSTRAP_TOKEN` | 引导管理员 token 明文（首次启动且无 admin 时写入哈希） | 空 |
| `CORS_ALLOW_ORIGINS` | 跨域来源（逗号分隔）；留空不启用 CORS（推荐同源反代部署） | 空 |

`.env.example` 是配置模板，开发环境中的 `.env` 不应提交到仓库。

## 项目结构

```text
AI_Question/
|-- pyproject.toml
|-- README.md
|-- .env.example
|-- docs/
|   |-- DEVELOP.md
|   |-- TESTING.md
|   `-- api/                  # 后端 API 文档（README + 自动生成的 OpenAPI/ReDoc）
|-- src/
|   |-- app/                 # 产物写盘（outputs）与任务执行入口（runner）
|   |-- api/                 # FastAPI 后端：鉴权、持久化、任务执行、路由
|   |-- agents/              # 命题、解题、审核、仲裁 Agent
|   |-- client/              # LLM 客户端与 provider 注册
|   |-- config/              # 环境变量配置
|   |-- engine/              # 状态机
|   |-- latex/               # LaTeX 后处理
|   |-- model/               # 状态类型、schema、统计
|   |-- prompts/             # YAML 提示词
|   |-- spec/                # 输入规格化（API 请求 → WorkflowData）
|   `-- utils/               # 通用文本与重试上下文工具
`-- tests/
    |-- test_api.py
    |-- test_auth.py
    |-- test_progress.py
    |-- test_stats_context.py
    |-- test_format.py
    |-- test_merger.py
    |-- test_outputs.py
    |-- test_parser.py
    |-- test_retry_context.py
    |-- test_review_fixes.py
    |-- test_settings.py
    |-- test_state_machine.py
    |-- test_template_agent.py
    |-- test_title.py
    `-- topics.py
```

## 测试

常用检查命令：

```bash
python -m compileall src tests
PYTHONPATH=src python -m pytest -q
```

更完整的测试目录说明、文件分工和新增测试约定见 [`TESTING.md`](TESTING.md)。
