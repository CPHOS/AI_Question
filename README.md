# CPhOS 物理竞赛题全自动生成系统

基于状态机编排的多 Agent AI 系统，自动生成 CPhO 决赛级物理竞赛大题。
系统将**命题**与**解题**拆分为独立 Agent，经数学 / 物理 / 结构三重审核与仲裁闭环后，输出可编译的 CPHOS LaTeX 文档。

> 详细的技术设计、工作流内部细节、提示词管理与 CPHOS 模板对齐说明等参见 [`docs/DEVELOP.md`](docs/DEVELOP.md)。

---

## 特性

- **命题 / 解题 Agent 独立** — 解题失败只重跑解题，不浪费一次完整题干生成
- **三轴并行审核 + 仲裁闭环** — 数学 / 物理 / 结构三份独立审核 → 仲裁 Agent 综合裁决（Function Calling 结构化输出）
- **分阶段重试计数** — `RETRY_PROBLEM` / `RETRY_SOLUTION` 各自独立计数；首轮通过不计 retry
- **4 种命题模式** — 自由命题 / 文献改编 / 思路拓展 / 简单题丰富，共用同一条状态机
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
# 编辑 .env，填入 LLM 服务商密钥和模型名称

# 3. 运行
uv run physics-generator --topic "刚体力学与角动量守恒"
uv run physics-generator --topic "电磁感应" --difficulty "省级竞赛"
uv run physics-generator --topic "电磁感应" --score 60
uv run physics-generator --input task.json
uv run physics-generator --adapt existing_problem.tex --mode problem_enrichment

# 4. 测试
uv run pytest -v
```

### 命题模式

| 模式 | CLI 用法 | 说明 |
|------|---------|------|
| `topic_generation` | `--topic "刚体力学"` | 自由命题：从主题出发创作全新竞赛题 |
| `literature_adaptation` | `--adapt paper.pdf --mode literature_adaptation` | 文献改编：基于学术文献改编为竞赛题 |
| `idea_expansion` | `--adapt sketch.txt --mode idea_expansion` | 思路拓展：从简要构想扩展为完整试题 |
| `problem_enrichment` | `--adapt simple.tex --mode problem_enrichment` | 题目丰富：在简单题基础上增加考察深度 |

不指定 `--mode` 时，系统根据是否提供 `--adapt` 自动推断。

### CLI 参数

```
physics-generator --topic TEXT           # 物理主题（与 --input/--adapt 互斥）
                  --input FILE           # 从 JSON 文件加载（与 --topic/--adapt 互斥）
                  --adapt FILE           # 基于已有材料改编（与 --topic/--input 互斥）
                  --difficulty TEXT       # 难度等级（默认: 国家集训队）
                  --score INT            # 题目总分（20-80，默认: 40）
                  --mode MODE            # 命题模式（topic_generation / literature_adaptation
                                         #           / idea_expansion / problem_enrichment）
                  --log                  # 追加运行记录到 TEST_LOG.md
```

### 环境变量

复制 `.env.example` 为 `.env` 并填入配置（必填项见 ★）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | LLM 服务商（`openrouter` / `openai_compatible`） | `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter API 密钥（仅 `openrouter`） | — |
| `LLM_API_KEY` / `LLM_BASE_URL` | OpenAI 兼容 API 密钥与地址（仅 `openai_compatible`） | — |
| ★ `BIG_MODEL_NAME` | 大模型（命题 / 审核 / 仲裁） | — |
| ★ `SMALL_MODEL_NAME` | 小模型（格式化排版） | — |
| `MAX_RETRY_COUNT` | 单阶段最大重试轮数 | `3` |
| `OUTPUT_DIR` | 输出目录 | `output` |

可调超参（默认即可，需要时再改）：`BIG_MODEL_TEMPERATURE` / `BIG_MODEL_MAX_TOKENS` /
`ARBITER_MAX_TOKENS` / `SMALL_MODEL_TEMPERATURE` / `SMALL_MODEL_MAX_TOKENS` /
`MODEL_TIMEOUT`。完整列表见 [`.env.example`](.env.example) 与 `docs/DEVELOP.md` 中的"配置参考"。

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

---

## 进一步阅读

- [`docs/DEVELOP.md`](docs/DEVELOP.md) — 工作流、状态机、节点说明、仲裁路由、LLM 调用预算、提示词管理、占位符流程、CPHOS 模板对齐、项目结构、技术栈

## 从旧版本升级

本仓库历史上做过一次大重构（包结构 + 模块路径变化）。已有的 `.env` 通常**无需改动即可继续运行**；如果你是从 `feat/architecture-restructure` 之前的版本（或 `main` 上的 `d355fe7` 及更早）升级，迁移要点见 [`docs/DEVELOP.md` 的"升级指引"](docs/DEVELOP.md#升级指引)。
