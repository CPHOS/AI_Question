# 远程 LaTeX 编译服务协议（v1）

本文件描述 AI_Question 主服务（**客户端**）与独立 **CPHOS LaTeX Compilation
Service**（**服务端**）之间的 HTTP 协议，并说明主服务侧的集成方式。把编译从主
服务进程剥离，可以：

- 让主服务无需安装 TeX 发行版、CPHOS 模板与字体；
- 把昂贵、易超时的编译放到可水平扩展、预装环境的专用服务。

> 本协议**镜像**编译服务自身的 API 契约（服务端 `docs/API.md` / `docs/openapi.json`，
> 版本 `1.0.0`）。以服务端契约为准；本文件仅补充客户端集成约定。

客户端实现见 `src/client/latex_service.py`，编译后端集成见 `src/app/outputs.py`
（`RemoteCompiler`）。当 `LATEX_COMPILER_BACKEND=remote` 时启用本协议；默认
`local` 走本机 subprocess，行为完全不变。

---

## 1. 核心模型

### 1.1 一作业一文档
服务端以「**一个作业编译一份文档**」为模型：每次 `POST /v1/compile` 上传一个
LaTeX 工程（一个入口 `.tex` 加任意相对路径的附属文件，或单个 `.zip`/`.tar.gz`
归档），服务编译出**一份 PDF**。无「多步骤单作业」概念——主服务若需先图片后正文，
则拆成多个作业依次提交（见 §5）。

上传的 multipart 部件字段名固定为 `files`；每个部件的 `filename` 即其在工程中的
相对路径（如 `task123.tex`、`task123_assets/fig1.pdf`），服务端原样保留目录结构。

### 1.2 入口探测
`entrypoint` 省略时服务端自动探测：单个 `.tex` 直接用；否则取名为 `main.tex` 者；
再否则取首个含 `\documentclass` 的文件。主服务总是**显式传入** `entrypoint`。

### 1.3 模板与字体
服务端镜像**预装** CPHOS 模板（`cphos.cls`、`cphos-e.cls` 等）与所需字体
（Times New Roman / SimSun / SimHei / KaiTi）。`use_cphos_templates=true`（默认）
时把模板搜索路径加入 Tectonic。客户端**不上传**模板。若工程内含同名文件，则上传
文件优先（作业工作目录最先被搜索）。

---

## 2. 作业状态机

```
queued ──> running ──> succeeded
                   └──> failed
```

| 状态 | 含义 |
| --- | --- |
| `queued` | 已受理，尚未开始。 |
| `running` | 正在编译。 |
| `succeeded` | 编译成功，PDF 可下载（`has_pdf=true`）。 |
| `failed` | 编译失败（详见 `/log` 与 `error` / `exit_code`）。 |

`succeeded` / `failed` 为终态。作业及其文件在 `JOB_TTL_SECONDS` 后被服务端的 TTL
清扫器回收（之后查询该作业返回 `404`）。

---

## 3. REST 接口（前缀 `/v1`）

### 鉴权
除 `GET /healthz` 外，所有端点需 API Key，二选一：

- `Authorization: Bearer <API_KEY>`（客户端采用此方式）；或
- `X-API-Key: <API_KEY>`。

### 3.1 `POST /v1/compile` — 提交作业

`Content-Type: multipart/form-data`：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `files` | file（可重复） | — | 工程文件（相对路径保留），或单个 `.zip`/`.tar.gz`。 |
| `entrypoint` | string | 自动 | 入口 `.tex` 相对路径。 |
| `use_cphos_templates` | bool | `true` | 是否加入 CPHOS 模板搜索路径。 |
| `synctex` | bool | `false` | 是否保留 SyncTeX 输出。 |
| `tectonic_args` | string | — | 允许清单内的额外 Tectonic 参数（空格分隔）。 |

**响应 `202 Accepted`**：`{ "job_id": "...", "status": "queued" }`。

### 3.2 `GET /v1/jobs/{job_id}` — 查询状态

- `200 OK`：`JobStatusResponse`：
  ```json
  {
    "job_id": "abc123",
    "status": "succeeded",
    "entrypoint": "task123.tex",
    "engine": "Tectonic 0.15.0",
    "created_at": "2026-05-30T12:00:00Z",
    "started_at": "2026-05-30T12:00:01Z",
    "finished_at": "2026-05-30T12:00:09Z",
    "duration_seconds": 8.1,
    "exit_code": 0,
    "error": null,
    "has_pdf": true
  }
  ```
- `404 Not Found`：作业不存在（含已过 TTL 回收）。

### 3.3 `GET /v1/jobs/{job_id}/result` — 下载产物

- 查询参数 `format`：`pdf`（默认，编译出的 PDF）或 `bundle`（全部产物的 zip）。
- `200 OK`：二进制产物。
- `409 Conflict`：作业尚未成功完成。

### 3.4 `GET /v1/jobs/{job_id}/log` — 下载编译日志

- `200 OK`：纯文本 Tectonic 日志。用于诊断失败。

### 3.5 `DELETE /v1/jobs/{job_id}` — 释放作业

客户端取回产物后可主动释放。`204 No Content`。即使不调用，服务端也会在 TTL 后
自动回收。本调用为 best-effort，客户端忽略其错误。

### 3.6 `GET /v1/version` — 服务与引擎版本

`200 OK`：服务版本与已安装的 Tectonic 引擎版本（用于探活 / 诊断）。

### 3.7 `GET /healthz` — 存活探针（无需鉴权）

---

## 4. 客户端配置

主服务通过以下设置（环境变量 / `/admin/settings`）接入：

| 设置键 | 默认 | 说明 |
| --- | --- | --- |
| `LATEX_COMPILER_BACKEND` | `local` | `local` 走本机 subprocess；`remote` 启用本协议。 |
| `LATEX_SERVICE_BASE_URL` | （空） | 编译服务根 URL，`remote` 时必填。 |
| `LATEX_SERVICE_API_KEY` | （空） | 编译服务 API Key，经 `Authorization: Bearer` 发送。 |
| `LATEX_SERVICE_POLL_INTERVAL` | `2.0` | 轮询作业状态的间隔（秒）。 |
| `LATEX_SERVICE_MAX_WAIT` | `1200.0` | 客户端等待作业终态的总截止（秒）。 |

> `/admin/settings` 返回中 `latex_service_api_key` 经脱敏（仅暴露
> `latex_service_api_key_set` 与末 4 位掩码），明文不外泄。

---

## 5. 主服务两阶段编译流程

`app.outputs` 的 `RemoteCompiler` 把统一的「构建步骤」接口（`compile_many`）映射为
**每步一个独立作业**。`write_outputs` 在 `remote` 后端下的行为：

1. **图片阶段**：对每个 TikZ standalone 源（`figN.tex`）各提交一个作业
   （`use_cphos_templates=false`，因 standalone 图片不依赖 CPHOS 模板）。据各作业
   终态填充 `figure_compile_status` 与 `available_figure_pdfs`，并把产出的
   `figN.pdf` 下载回 `*_assets/`。
2. **占位重写**：本地按可用图片集合执行 `_rewrite_figure_asset_paths`，缺图位置
   替换为可编译占位（逻辑与 `local` 后端一致）。
3. **最终文档阶段**：把最终 `.tex` 作为单个作业提交
   （`use_cphos_templates=true`），并把第 1 阶段得到的可用图片 PDF 作为附属文件
   一并上传（相对路径如 `task123_assets/fig1.pdf`），下载产出 PDF 回工作区。

由于每个作业相互独立，最终文档作业必须**随上传**其引用的图片 PDF。该两阶段法保证
「缺图回退」结果确定，且与本机后端的占位逻辑 1:1 一致。
