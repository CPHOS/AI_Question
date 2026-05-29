# 后端 API 文档

CPhOS 物理竞赛题全自动生成系统的 HTTP 后端接口文档。

本目录中的 `openapi.json` 与 `index.html` 由项目**自动生成**，请勿手工编辑；
更新方式见下文「重新生成」。

## 文件说明

| 文件 | 内容 |
|------|------|
| `openapi.json` | 机器可读的 OpenAPI 3.x 规范（由 FastAPI 应用导出） |
| `index.html` | 基于 ReDoc 的离线可浏览文档（内联规范，可直接用 file:// 打开） |
| `README.md` | 本说明（鉴权、工作流、示例） |

服务运行时也实时暴露同一份规范：

- Swagger UI: `http://<host>:<port>/docs`
- ReDoc: `http://<host>:<port>/redoc`
- 原始规范: `http://<host>:<port>/openapi.json`

## 启动服务

```bash
# 1. 配置 .env（在 .env.example 基础上）：
#    API_HOST / API_PORT / MAX_CONCURRENT_JOBS / DB_PATH / ADMIN_BOOTSTRAP_TOKEN
# 2. 启动
uv run physics-api
# 或开发模式
uv run uvicorn api.app:app --reload
```

首次启动时，若配置了 `ADMIN_BOOTSTRAP_TOKEN` 且库内尚无管理员，系统会以该
token 的哈希写入一个引导管理员。该明文 token 由部署者保管，用于调用管理端点。

## 鉴权

所有 `/api/**` 端点都需要在请求头携带 Bearer token：

```
Authorization: Bearer <token>
```

- token 为**随机不透明串**，由管理员通过 `POST /api/admin/tokens` 签发；
- 服务端只保存 token 的 SHA-256 哈希，**明文仅在创建时返回一次**，需通过安全
  渠道分发；
- 角色分 `user` 与 `admin`；管理员拥有全权限（可查看 / 下载 / 删除任意用户的
  任务与产物）。

## 异步轮询工作流

生成任务耗时通常为数分钟，因此采用「提交 → 轮询 → 下载」模式：

```
POST /api/tasks            ──► 返回 task_id（status=queued）
        │
        ▼
GET  /api/tasks/{task_id}   ──► 轮询，status: queued → running → done/error/aborted
        │                       （响应含 phase 字段，标识当前所处工作流阶段）
        ▼
GET  /api/tasks/{task_id}/progress           ──► 节点级进度时间线（阶段事件 + 格式化产出）
GET  /api/tasks/{task_id}/artifacts          ──► 产物清单
GET  /api/tasks/{task_id}/artifacts/{name}   ──► 下载产物
GET  /api/tasks/{task_id}/result             ──► 内联返回 final_latex / report
```

### 节点级进度

`GET /api/tasks/{id}/progress` 返回任务的阶段事件时间线，可在轮询期间用于展示
"当前跑到哪个节点"以及各阶段的结构化结果：

- 每个阶段产生一对事件：`running`（进入阶段）与 `completed`（产出就绪）；
- `completed` 事件的 `output` 字段是按**阶段类型**格式化的结构化快照（如审核阶段
  给出四路审核意见、仲裁阶段给出裁决与重试计数、排版阶段给出公式/插图计数），
  **不是模型原始输出**；长文本会截断并以 `truncated` / `length` 标注；
- 重试会使同名阶段多次出现，按单调递增的 `seq` 区分；
- 完整成品（如 LaTeX）请通过产物下载端点获取，进度仅给规模统计。

## 端点速览

### 任务（user / admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/me` | 返回当前 token 的身份与角色（任意有效 token） |
| POST | `/api/tasks` | 提交生成任务（topic 或 source_material） |
| POST | `/api/tasks/upload` | 上传源材料文件并提交改编任务 |
| GET | `/api/tasks` | 列出当前用户任务历史（分页 + 过滤/排序） |
| GET | `/api/tasks/{id}` | 查询状态与摘要（含 `phase`） |
| GET | `/api/tasks/{id}/progress` | 节点级进度时间线（阶段事件 + 格式化产出） |
| GET | `/api/tasks/{id}/events` | 实时进度推送（SSE，`text/event-stream`） |
| GET | `/api/tasks/{id}/artifacts` | 列出产物 |
| GET | `/api/tasks/{id}/artifacts/archive` | 打包下载全部产物（`application/zip`） |
| GET | `/api/tasks/{id}/artifacts/{name}` | 下载单个产物（按类型设置 MIME + Content-Disposition；`?disposition=inline` 可内联预览 PDF） |
| GET | `/api/tasks/{id}/result` | 内联返回关键文本结果 + 编译状态 |
| POST | `/api/tasks/{id}/compile` | 按需（重）编译最终 LaTeX 与图片为 PDF（不调用 LLM） |
| POST | `/api/tasks/{id}/cancel` | 中止运行中 / 排队中的任务（协作式取消） |
| POST | `/api/tasks/{id}/retry` | 以原输入克隆并重跑 error / aborted / interrupted 任务 |
| DELETE | `/api/tasks/{id}` | 删除任务及产物 |

### 管理（仅 admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/users` | 创建用户 |
| GET | `/api/admin/users` | 列出用户（分页 + 模糊搜索/排序） |
| GET | `/api/admin/users/{id}` | 查看单用户详情（含 token 数 / 任务数） |
| PATCH | `/api/admin/users/{id}` | 更新用户备注名（label） |
| DELETE | `/api/admin/users/{id}` | 删除用户（级联 token / 任务 / 产物） |
| GET | `/api/admin/users/{id}/tasks` | 查看指定用户任务历史（分页） |
| POST | `/api/admin/tokens` | 为用户签发 token（返回一次性明文） |
| GET | `/api/admin/tokens` | 列出 token 元数据（分页 + 模糊搜索/排序） |
| DELETE | `/api/admin/tokens/{id}` | 吊销 token |
| GET | `/api/admin/tasks` | 跨用户列出全部任务（分页 + 过滤/排序） |
| GET | `/api/admin/stats` | 管理端统计概览（任务/用户/token/用量与费用） |
| GET / POST | `/api/admin/llm/providers` | 列出 / 创建 LLM 服务商凭据（api_key 脱敏） |
| GET / PATCH / DELETE | `/api/admin/llm/providers/{id}` | 查看 / 更新 / 删除服务商 |
| GET / POST | `/api/admin/llm/models` | 列出 / 创建模型配置 |
| GET / PATCH / DELETE | `/api/admin/llm/models/{id}` | 查看 / 更新 / 删除模型配置 |
| GET | `/api/admin/llm/agents` | 列出 Agent → 模型配置绑定 |
| PUT | `/api/admin/llm/agents/{role}` | 绑定某 Agent 到模型配置 |
| GET / PATCH | `/api/admin/llm/settings` | 查看 / 更新运行期应用设置 |
| GET | `/api/admin/llm/provider-kinds` | 列出注册中心已注册的服务商类型（kind） |
| GET | `/api/admin/llm/options` | 聚合元数据：服务商类型 / Agent 角色 / 应用设置项 |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（无需鉴权，返回组件级 db / worker 状态） |
| GET | `/version` | 版本与许可证信息（无需鉴权） |

### 分页、过滤与排序

列表端点（`/api/tasks`、`/api/admin/tasks`、`/api/admin/users`、
`/api/admin/users/{id}/tasks`、`/api/admin/tokens`）统一返回包装对象：

```json
{ "items": [ ... ], "total": 123, "limit": 50, "offset": 0 }
```

任务列表（`/api/tasks` 与 `/api/admin/tasks`）额外支持查询参数：

- `status`：按状态过滤，可逗号分隔（如 `status=running,error`）；
- `mode`：按命题模式过滤；
- `q`：按 topic 模糊搜索；
- `order`：按创建时间排序，`ASC` / `DESC`（默认 `DESC`）。

管理端的用户与 token 列表（`/api/admin/users`、`/api/admin/tokens`）支持：

- `q`：模糊搜索（用户匹配 `user_id` / `label`；token 匹配 `id` / `label`）；
- `order`：按创建时间排序，`ASC` / `DESC`（默认 `DESC`）；
- `/api/admin/tokens` 另支持 `user_id` 精确过滤。

### 错误响应

所有显式抛出的业务错误统一返回 `{ "code": ..., "detail": ... }`：`code` 为稳定的
机器可读错误码（便于前端分支处理），`detail` 为面向人类的说明。

| 状态码 | 典型 `code` |
|--------|-------------|
| 401 | `unauthorized` |
| 403 | `forbidden` |
| 404 | `not_found` / `task_not_found` |
| 409 | `task_not_terminal` / `task_already_terminal` / `task_not_retryable` / `final_latex_missing` / `compile_in_progress` |
| 422 | `unprocessable_entity`（业务校验） |

> 注意：FastAPI 的**请求体结构校验**错误（同为 `422`）保留框架默认的结构化
> `{ "detail": [ { "loc": ..., "msg": ... } ] }` 形态，以保留字段级定位信息。

### 取消任务

`POST /api/tasks/{id}/cancel` 为**协作式取消**：

- 仍在队列中未启动的任务被立即标记为 `aborted`；
- 运行中的任务被标记为 `aborting`，工作流在下一个**阶段边界**停止后落为 `aborted`；
- 已处于终态（`done` / `error` / `aborted` / `interrupted`）的任务返回 `409`。

响应 `202 { "task_id": "...", "status": "aborting" | "aborted" }`。

### 实时进度（SSE）

`GET /api/tasks/{id}/events` 返回 `text/event-stream`，作为轮询 `/progress` 的低延迟
替代（客户端不支持时回退轮询）。帧类型：

- `: connected` —— 建立连接时下发的注释帧（就绪/心跳标记，无 `event` 字段）；
- `event: phase` —— 阶段事件，`data` 为 `{seq, phase, phase_label, status, output, created_at}`；
- `event: status` —— 任务进入终态时推送 `{status}` 并关闭连接（任务被删除时为 `{"status": "deleted"}`）。

### 跨域（CORS）

推荐用 nginx 反代 `/api`、`/health` 做**同源**部署（无需 CORS）。若确需跨域，
在 `.env` 设置 `CORS_ALLOW_ORIGINS`（逗号分隔的来源列表）后，后端会启用
`CORSMiddleware`（允许 `Authorization` 头、`GET/POST/DELETE/PATCH/OPTIONS`
方法，并 `expose` `Content-Disposition`）。

## 产物文件

每个任务在 `OUTPUT_DIR/{user_id}/` 下生成（产物逻辑名见下表）：

| 逻辑名 | 文件 | 内容 |
|--------|------|------|
| `final_latex` | `{task_id}_final.tex` | 可编译的 CPHOS LaTeX 成品 |
| `final_pdf` | `{task_id}_final.pdf` | 最终 PDF（启用自动编译且成功时） |
| `draft` | `{task_id}_draft.md` | 大模型原始草稿 |
| `tagged` | `{task_id}_tagged.md` | 占位符文本（调试用） |
| `log` | `{task_id}_log.json` | 完整运行日志 |
| `report` | `{task_id}_report.md` | 仲裁报告 |
| `assets/...` | `{task_id}_assets/*` | 插图绘制需求 / TikZ 草稿 / PDF |

## LaTeX 编译与 PDF 预览

编译在生成流程中按服务端设置自动执行：

- `AUTO_COMPILE_FIGURES`（默认 `true`）：把 `figN.tex` 编译为 `figN.pdf`；
- `AUTO_COMPILE_LATEX`（默认 **`false`**）：把最终题面编译为 `{task_id}_final.pdf`。

因此默认情况下**不会**自动产出 `final_pdf`。提供两种补充能力：

### 编译状态

`GET /api/tasks/{id}/result` 除 `final_latex` / `report` 外，额外返回：

```json
{
  "latex_compile_status": { "ok": true, "detail": "", "template_dir": "..." },
  "figure_compile_status": { "fig1": { "ok": true, "detail": "" } },
  "final_pdf_available": true
}
```

- `latex_compile_status` / `figure_compile_status` 源自 `_log.json`，未编译时为空对象；
- `final_pdf_available` 表示 `final_pdf` 产物当前是否存在（可下载 / 预览）。

### 按需编译

`POST /api/tasks/{id}/compile` 对**已生成的** `final_latex` 与图片 TikZ 源重新编译为
PDF（**不调用 LLM**），用于默认未编译或需刷新 PDF 的场景：

- 要求任务已处于终态且存在 `final_latex` 产物，否则 `409`；
- 同一任务的编译会串行化，重入返回 `409`；
- 返回 `TaskCompileResult`（`latex_compile_status` / `figure_compile_status` /
  `final_pdf_available`）。
- 注意：最终 LaTeX 中的图片引用 / 占位在生成时已固定，本端点不重写引用；若需据
  新编译出的图片刷新引用，请重跑生成任务。

> 编译后端由 `LATEX_COMPILER_BACKEND` 决定（`local` 本机 / `remote` 独立服务，
> 协议见 `docs/latex-service-protocol.md`），两者对上述接口透明。

### PDF 内联预览

`GET /api/tasks/{id}/artifacts/{name}` 默认以 `Content-Disposition: attachment`
触发下载。传 `?disposition=inline` 则以 `inline` 返回（PDF 配合
`Content-Type: application/pdf`），可在浏览器 `<iframe>` / `<embed>` 中直接预览：

```
GET /api/tasks/{id}/artifacts/final_pdf?disposition=inline
GET /api/tasks/{id}/artifacts/assets/fig1.pdf?disposition=inline
```

## curl 示例

```bash
BASE=http://localhost:8000
ADMIN=<bootstrap-admin-token>

# 1. 管理员创建用户
USER_ID=$(curl -s -X POST "$BASE/api/admin/users" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"label":"team-a"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["user_id"])')

# 2. 为该用户签发 token（明文仅此一次返回）
USER_TOKEN=$(curl -s -X POST "$BASE/api/admin/tokens" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$USER_ID\",\"role\":\"user\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 3. 用户提交生成任务
TASK_ID=$(curl -s -X POST "$BASE/api/tasks" \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"topic":"刚体力学与角动量守恒","total_score":40}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')

# 4. 轮询状态
curl -s "$BASE/api/tasks/$TASK_ID" -H "Authorization: Bearer $USER_TOKEN"

# 5. 完成后下载最终 LaTeX
curl -s "$BASE/api/tasks/$TASK_ID/artifacts/final_latex" \
  -H "Authorization: Bearer $USER_TOKEN" -o final.tex
```

## 重新生成

接口变更后，重新导出本目录的静态文档：

```bash
uv run physics-api-docs            # 写入 docs/api
uv run physics-api-docs --out DIR  # 写入指定目录
```

该命令从 `api.app:app` 读取 OpenAPI 规范，因此始终与代码保持一致。
