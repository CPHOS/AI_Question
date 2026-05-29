# 远程 LaTeX 编译服务协议（v1）

本文件定义 AI_Question 主服务（**客户端**）与独立 **LaTeX 编译服务**（**服务端**）
之间的 HTTP 协议。把编译从主服务进程剥离，可以：

- 让主服务无需安装 TeX 发行版、CPHOS 模板与字体；
- 把昂贵、易超时的编译放到可水平扩展、预装环境的专用服务；
- 通过统一的「工作区 + 构建计划」语义同时支持图片（standalone TikZ）与最终文档。

客户端实现见 `src/client/latex_service.py`，编译后端集成见 `src/app/outputs.py`
（`RemoteCompiler`）。当 `LATEX_COMPILER_BACKEND=remote` 时启用本协议；默认
`local` 走本机 subprocess，行为完全不变。

---

## 1. 核心模型

### 1.1 工作区（workspace）
一次编译作业对应一个**虚拟工作区**：一组带相对路径的文件，可包含子目录。
上传文件的 `filename` 即其在工作区中的相对路径（如 `task123.tex`、
`task123_assets/fig1.tex`）。服务端须按该相对路径还原目录结构后再编译。

### 1.2 构建计划（manifest）
描述如何编译工作区，JSON 结构：

```json
{
  "engine": "xelatex",
  "timeout_seconds": 180,
  "steps": [
    { "id": "fig1", "root": "fig1.tex", "passes": 1 },
    { "id": "final", "root": "task123.tex", "passes": 2 }
  ],
  "outputs": ["fig1.pdf", "task123.pdf"]
}
```

字段说明：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `engine` | string | LaTeX 引擎名（如 `xelatex`）。 |
| `timeout_seconds` | int | **单步**编译子进程超时（秒）。 |
| `steps` | array | 有序构建步骤，服务端须**按数组顺序串行**执行。 |
| `steps[].id` | string | 步骤标识，回执中据此匹配结果（须唯一）。 |
| `steps[].root` | string | 待编译入口文件的工作区相对路径。 |
| `steps[].passes` | int | 对该 `root` 连续编译的遍数（用于交叉引用）。某遍失败即停止后续遍。 |
| `outputs` | array | 期望产物的工作区相对路径（通常为各 `root` 的 `.pdf`）。 |

**步骤执行约定**：对步骤 `root`，工作目录设为 `dirname(root)`，编译实参为
`basename(root)`。因此 `task123_assets/fig1.tex` 在 `task123_assets/` 目录内编译，
产物 `fig1.pdf` 落在同目录 → 工作区相对路径 `task123_assets/fig1.pdf`。

**串行与可见性**：步骤按顺序执行，先编译的产物（图片 PDF）对后续步骤（最终文档）
在工作区中可见。这使「单作业多步骤」可一次完成图片 + 文档。

### 1.3 模板与字体
服务端**预装** CPHOS 模板（`cphos.cls` 等）与所需字体，客户端不上传模板。
最终文档步骤无需在 manifest 中声明模板路径，由服务端环境（如 `TEXINPUTS`）提供。

---

## 2. 作业状态机

```
queued ──> running ──> completed
                   └──> failed
(任意状态超过 TTL) ──> expired
```

| 状态 | 含义 |
| --- | --- |
| `queued` | 已受理，尚未开始。 |
| `running` | 正在执行构建计划。 |
| `completed` | 构建计划**已跑完**（不代表每步都成功，见 `steps[].ok`）。 |
| `failed` | **基础设施级**失败（引擎缺失、工作区损坏等）。 |
| `expired` | 作业及产物已过 TTL 被回收。 |

> **重要**：单个步骤的 LaTeX 编译失败**不**使作业变 `failed`，也**不**返回 5xx；
> 作业仍为 `completed`，失败信息体现在该步骤的 `ok=false` 与 `log_tail`。`failed`
> 仅用于服务端无法执行计划的情形。

---

## 3. REST 接口（前缀 `/v1`）

所有作业相关响应体共享如下结构：

```json
{
  "job_id": "abc123",
  "status": "completed",
  "poll_interval_seconds": 2,
  "steps": [
    {
      "id": "final",
      "ok": true,
      "log_tail": "...最后若干行日志...",
      "produced": ["task123.pdf"]
    }
  ],
  "artifacts": [
    { "name": "task123.pdf", "media_type": "application/pdf", "size": 24576 }
  ],
  "error": null
}
```

- `steps[].produced`：该步骤实际产出的工作区相对路径列表。
- `artifacts[].name`：可下载产物的工作区相对路径（即下载端点标识）。
- `poll_interval_seconds`：服务端建议的轮询节流（可选）；客户端取其与本地配置较大值。
- `error`：仅 `failed` 时为对象（`{"message": "..."}`），否则 `null`。

### 3.1 `POST /v1/compile` — 提交作业

`Content-Type: multipart/form-data`：

- 表单字段 `manifest`：构建计划 JSON 字符串（§1.2）。
- 重复部件 `file`：每个为一个工作区文件，其 `filename` = 工作区相对路径。

**响应 `202 Accepted`**：作业快照（通常 `status=queued`，含 `job_id`）。

### 3.2 `GET /v1/compile/{job_id}` — 查询状态

- `200 OK`：作业快照。
- `404 Not Found`：作业不存在。
- `410 Gone`：作业已过期（等价 `status=expired`）。

### 3.3 `GET /v1/compile/{job_id}/artifacts/{name}` — 下载产物

`name` 为工作区相对路径，须 URL 编码（`urllib.parse.quote(name, safe='')`）。

- `200 OK`：二进制产物（`Content-Type` 取 `artifacts[].media_type`）。
- `404 Not Found`：作业或产物不存在。

### 3.4 `DELETE /v1/compile/{job_id}` — 释放作业

客户端取回产物后可主动释放。`204 No Content`。即使不调用，服务端也应在 TTL 后
自动回收（`expired`）。本调用为 best-effort，客户端忽略其错误。

### 3.5 `GET /healthz` — 健康与能力

返回服务可用性与能力信息，便于客户端探活与诊断：

```json
{
  "status": "ok",
  "engines": ["xelatex"],
  "template_version": "cphos-2024.1"
}
```

---

## 4. 鉴权（预留）

当前版本**不强制鉴权**。协议预留 `Authorization: Bearer <token>` 头位；后续可在
不破坏路径与语义的前提下启用。客户端实现保留注入该头的扩展点。

---

## 5. 客户端配置

主服务通过以下设置（环境变量 / `/admin/settings`）接入：

| 设置键 | 默认 | 说明 |
| --- | --- | --- |
| `LATEX_COMPILER_BACKEND` | `local` | `local` 走本机 subprocess；`remote` 启用本协议。 |
| `LATEX_SERVICE_BASE_URL` | （空） | 编译服务根 URL，`remote` 时必填。 |
| `LATEX_SERVICE_POLL_INTERVAL` | `2.0` | 轮询作业状态的间隔（秒）。 |
| `LATEX_SERVICE_MAX_WAIT` | `1200.0` | 客户端等待作业终态的总截止（秒）。 |
| `LATEX_COMPILE_TIMEOUT` | `180` | 单步编译超时（秒），写入 manifest 的 `timeout_seconds`。 |

---

## 6. 主服务两阶段编译流程

`app.outputs.write_outputs` 在 `remote` 后端下的行为：

1. **图片阶段**：把所有 TikZ standalone 源（`figN.tex`）作为一个多步骤作业
   （每步 `passes=1`）提交。据各步 `ok` 与产物可用性填充 `figure_compile_status`
   与 `available_figure_pdfs`，并把产出的 `figN.pdf` 下载回 `*_assets/`。
2. **占位重写**：本地按可用图片集合执行 `_rewrite_figure_asset_paths`，缺图位置
   替换为可编译占位（逻辑与 `local` 后端一致）。
3. **最终文档阶段**：把最终 `.tex` 作为单步骤作业（`passes=2`）提交，并把第 1 阶段
   得到的可用图片 PDF 作为 `assets` 一并上传；下载产出 PDF 回工作区。

该两阶段法保证「缺图回退」结果确定，且与本机后端的占位逻辑 1:1 一致。
