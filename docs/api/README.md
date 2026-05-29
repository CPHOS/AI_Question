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
| POST | `/api/tasks` | 提交生成任务（topic 或 source_material） |
| POST | `/api/tasks/upload` | 上传源材料文件并提交改编任务 |
| GET | `/api/tasks` | 列出当前用户任务历史 |
| GET | `/api/tasks/{id}` | 查询状态与摘要（含 `phase`） |
| GET | `/api/tasks/{id}/progress` | 节点级进度时间线（阶段事件 + 格式化产出） |
| GET | `/api/tasks/{id}/artifacts` | 列出产物 |
| GET | `/api/tasks/{id}/artifacts/{name}` | 下载单个产物 |
| GET | `/api/tasks/{id}/result` | 内联返回关键文本结果 |
| DELETE | `/api/tasks/{id}` | 删除任务及产物 |

### 管理（仅 admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/users` | 创建用户 |
| GET | `/api/admin/users` | 列出用户 |
| GET | `/api/admin/users/{id}/tasks` | 查看指定用户任务历史 |
| POST | `/api/admin/tokens` | 为用户签发 token（返回一次性明文） |
| GET | `/api/admin/tokens` | 列出 token 元数据 |
| DELETE | `/api/admin/tokens/{id}` | 吊销 token |
| GET | `/api/admin/tasks` | 跨用户列出全部任务 |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（无需鉴权） |

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
