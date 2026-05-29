# CPhOS 物理竞赛题全自动生成系统

基于状态机编排的多 Agent AI 系统，自动生成 CPhO 决赛级物理竞赛大题。
系统将**命题**与**解题**拆分为独立 Agent，经数学 / 物理 / 结构 / 质量并行审核与仲裁闭环后，输出可编译的 CPHOS LaTeX 文档。

> 详细的技术设计、工作流内部细节、提示词管理与 CPHOS 模板对齐说明等参见 [`docs/DEVELOP.md`](docs/DEVELOP.md)。

---

## 特性

- **命题 / 解题 Agent 独立** — 解题失败时只重跑解题阶段，保留已通过审核的题干生成结果
- **四路并行审核 + 仲裁闭环** — 数学 / 物理 / 结构 / 质量四份独立审核 → 仲裁 Agent 综合裁决（Function Calling 结构化输出）
- **分阶段重试计数** — `RETRY_PROBLEM` / `RETRY_SOLUTION` 各自独立计数；首轮通过不计 retry
- **4 种命题模式** — 自由命题 / 文献改编 / 思路拓展 / 简单题丰富，共用同一条状态机
- **CPHOS 题量风格提示** — 40 分题默认倾向 2-3 个一级小问、3-5 个叶子小问；复杂推导或原题充实任务由质量审核判断是否需要保留更多小问
- **CPHOS 模板对齐** — 直接产出可编译的 `\documentclass[answer]{cphos}` 文档（公式编号 / 评分点 / 多级小问标记齐全）
- **多服务商客户端** — 基于注册中心的 LLM 客户端抽象（OpenRouter / 任何 OpenAI 兼容 API），新增服务商只需 `@register_provider("name")`

---

## 快速开始

> 需要 Python ≥ 3.11 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
# 编辑 .env，填入服务商密钥种子、引导管理员 token 等（详见"配置"）

# 3. 启动后端 API 服务
uv run physics-api

# 4. 测试
uv run pytest -v
```

本系统为**纯后端服务**：所有任务均通过 FastAPI 接口提交与调度，不提供命令行入口。
启动后访问 `http://<API_HOST>:<API_PORT>/docs` 查看交互式 API 文档，或见
[`docs/api/README.md`](docs/api/README.md) 的接口速查与 `curl` 示例。

### 命题模式

| 模式 | 提交方式 | 说明 |
|------|---------|------|
| `topic_generation` | `POST /api/tasks`，`topic` 必填 | 自由命题：从主题出发创作全新竞赛题 |
| `literature_adaptation` | `POST /api/tasks` 带 `source_material`，或 `POST /api/tasks/upload` | 文献改编：基于学术文献改编为竞赛题 |
| `idea_expansion` | 同上，`mode=idea_expansion` | 思路拓展：从简要构想扩展为完整试题 |
| `problem_enrichment` | 同上，`mode=problem_enrichment` | 题目丰富：在简单题基础上增加考察深度 |

不指定 `mode` 时，系统根据是否提供 `source_material` 自动推断。

### CPHOS 题量风格

系统默认按近届 CPHOS 联考理论题的排版和题量习惯生成题目：

- 40 分题优先使用 2-3 个一级小问，叶子小问通常控制在 3-5 个。
- 复杂推导、分段讨论或原题充实任务可以保留更多叶子小问，质量审核会关注是否存在可合并的碎步。
- 同一推导链上的定义、代入、近似和结论应合并到同一个小问中。
- 小问编号单独起段并位于行首，编号后同一行接正文，例如 `（1.1）推导...`。

这些规则同时写入命题规划、命题生成、解答生成提示词。结构审核会统计一级小问和叶子小问数量，并在 40 分题明显偏多时给出提示；是否重试由质量审核和仲裁结合题目深度、推导链条和源材料任务共同判断。

### 环境变量

复制 `.env.example` 为 `.env` 并填入配置（必填项见 ★）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | 默认服务商（`openrouter` / `openai_compatible`） | `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter API 密钥（仅 `openrouter`） | — |
| `LLM_API_KEY` / `LLM_BASE_URL` | OpenAI 兼容 API 密钥与地址（仅 `openai_compatible`） | — |
| ★ `BIG_MODEL_NAME` | 大模型（命题 / 审核 / 仲裁） | — |
| ★ `SMALL_MODEL_NAME` | 小模型（格式化排版） | — |
| `MAX_RETRY_COUNT` | 单阶段最大重试轮数 | `3` |
| `OUTPUT_DIR` | 输出目录 | `output` |

> **注意**：模型与服务商选择及其参数、流程开关等业务可调项现以**数据库内可持久化
> 设置**存储，上表中相关变量**仅在数据库首次初始化时**作为种子默认值写入；之后请
> 通过管理员 API（`/api/admin/llm/*`）查看与修改。`OUTPUT_DIR` 等部署项仍始终读 `.env`。

可调超参（默认即可，需要时再改）：`BIG_MODEL_TEMPERATURE` / `BIG_MODEL_MAX_TOKENS` /
`ARBITER_MAX_TOKENS` / `REVIEW_TEMPERATURE` / `SMALL_MODEL_TEMPERATURE` /
`SMALL_MODEL_MAX_TOKENS` / `MODEL_TIMEOUT`。完整列表与可持久化设置说明见
[`.env.example`](.env.example) 与 `docs/DEVELOP.md` 中的"配置项"。

---

## API 服务

系统提供基于 FastAPI 的后端 API，支持多用户、Token 鉴权、异步任务与
历史产物管理。

```bash
# 1. 在 .env 中配置 API_HOST / API_PORT / MAX_CONCURRENT_JOBS / DB_PATH / ADMIN_BOOTSTRAP_TOKEN
#    （如需跨域可另设 CORS_ALLOW_ORIGINS）
# 2. 启动服务
uv run physics-api
# 或开发模式
uv run uvicorn api.app:app --reload
```

- 鉴权：所有 `/api/**` 端点需 `Authorization: Bearer <token>`；token 由管理员通过
  `/api/admin/tokens` 签发并手动分发。
- 工作流：`POST /api/tasks` 提交 → 轮询 `GET /api/tasks/{id}`（或订阅
  `GET /api/tasks/{id}/events` SSE）→ 下载产物；可随时 `POST /api/tasks/{id}/cancel` 取消。
- 在线文档：运行后访问 `/docs`（Swagger）或 `/redoc`；离线文档见 [`docs/api`](docs/api)。
- 重新生成 API 文档：`uv run physics-api-docs`。
- 公开探活：`GET /health` 健康检查、`GET /version` 版本与许可证信息（均无需鉴权）。

完整端点、鉴权与示例见 [`docs/api/README.md`](docs/api/README.md)。

---

## 输出文件
每次运行在 `output/` 下生成：

| 文件 | 内容 |
|------|------|
| `{task_id}_final.tex` | 可直接编译的 CPHOS LaTeX 成品 |
| `{task_id}_draft.md` | 大模型原始草稿（题干 + 解答） |
| `{task_id}_tagged.md` | 占位符文本（调试用） |
| `{task_id}_log.json` | 完整运行日志（裁决、理由、审核意见、模板报告等） |
| `{task_id}_report.md` | 仲裁报告 |
| `{task_id}_assets/README.md` | 插图绘制需求（仅题目含图时生成） |
| `{task_id}_assets/figN.tex` | 可编辑 TikZ 草稿（仅题目含图时生成） |
| `{task_id}_assets/figN.pdf` | 外置插图 PDF（启用自动图片编译且编译成功时生成） |

---

## 进一步阅读

- [`docs/DEVELOP.md`](docs/DEVELOP.md) — 工作流、节点职责、仲裁路由、状态数据、LLM 客户端、提示词管理、LaTeX 后处理、CPHOS 模板约定、配置项、项目结构
- [`docs/TESTING.md`](docs/TESTING.md) — 测试目录说明、常用命令、Mock 约定和新增测试指南
- [`docs/api/README.md`](docs/api/README.md) — 后端 API 鉴权、端点、异步工作流与产物管理（含自动生成的 OpenAPI 规范）

## 从旧版本升级

本仓库历史上做过一次大重构（包结构 + 模块路径变化）。已有的 `.env` 通常**无需改动即可继续运行**；如果你是从 `feat/architecture-restructure` 之前的版本（或 `main` 上的 `d355fe7` 及更早）升级，迁移要点见 [`docs/DEVELOP.md` 的"升级指引"](docs/DEVELOP.md#升级指引)。

## 许可证

本项目以 [GNU Affero 通用公共许可证 v3.0 或更高版本](LICENSE)（AGPL-3.0-or-later）授权。
