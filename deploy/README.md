# 后端部署（Docker + nginx）

本目录与仓库根的 `Dockerfile` / `docker-compose.yml` / `.dockerignore` 一起，
提供 CPhOS AI 命题系统**后端**的容器化部署。

## 组件

| 文件 | 作用 |
| --- | --- |
| `Dockerfile`（仓库根） | 后端镜像：uv + Python 3.11，从 `/app` 源码树以可编辑安装运行 |
| `docker-compose.yml`（仓库根） | 编排 `api`（后端）+ `nginx`（反向代理） |
| `deploy/nginx/default.conf` | nginx 反代规则，含 SSE 调优与上传体积上限 |
| `.dockerignore`（仓库根） | 构建上下文裁剪 |

## 先决条件

1. 已安装 Docker / Docker Compose。
2. **拉取子模块**（FormatChecker 格式检查工具）：
   ```bash
   git submodule update --init
   ```
   未初始化时格式检查会优雅降级跳过，不影响生成。
3. 准备 `.env`（参考根目录 `.env.example`）。容器部署建议：
   - `OPENROUTER_API_KEY=...`（或对应服务商密钥）
   - `ADMIN_BOOTSTRAP_TOKEN=<随机长串>`（首次启动写入引导管理员）
   - `LATEX_COMPILER_BACKEND=remote` + `LATEX_SERVICE_BASE_URL` / `LATEX_SERVICE_API_KEY`
     （推荐把编译卸载到独立编译服务，避免在后端镜像内塞入庞大的 TeX Live）
   - 同源反代部署时 `CORS_ALLOW_ORIGINS` 留空即可。

## 启动

```bash
docker compose up -d --build
```

- 入口：`http://<host>/`
- API：`http://<host>/api/...`，OpenAPI 文档：`http://<host>/docs`
- 健康检查：`http://<host>/health`

数据持久化在宿主机：

- `./data` → 容器 `/app/data`（SQLite：用户/token/任务/LLM 设置）
- `./output` → 容器 `/app/output`（生成的 .tex/.pdf/报告）

## 关键约束

- **单实例运行**：`api` 服务的任务执行器与 SSE 进度推送是**进程内状态**，
  请勿设置 `deploy.replicas>1` 或 uvicorn 多 worker，否则
  `MAX_CONCURRENT_JOBS` 与实时进度会失效。
- **源码树运行**：镜像在 `/app` 下以可编辑方式安装，`config.PROJECT_ROOT`
  据此解析 `data/`、`output/`、`external/FormatChecker`。请勿改动工作目录布局。
- **SSE 反代**：nginx 对 `/api/tasks/{id}/events` 关闭 `proxy_buffering` 并放长
  读超时；若叠加其它代理层（CDN/网关）需同样关闭缓冲。

## 本机 LaTeX 编译（可选）

默认镜像不含 TeX Live。若确需在后端容器内本机编译（`LATEX_COMPILER_BACKEND=local`）：

1. 构建时安装 TeX Live：
   ```bash
   docker compose build --build-arg INSTALL_TEXLIVE=true
   ```
2. 在 `docker-compose.yml` 取消 CPHOS 模板卷的注释，把 `CPHOS-Latex` 仓库挂入：
   ```yaml
   - ../CPHOS-Latex:/CPHOS-Latex:ro
   ```
   容器内 `PROJECT_ROOT=/app`，默认 `CPHOS_TEMPLATE_DIR=../CPHOS-Latex/theory`
   即解析为 `/CPHOS-Latex/theory`（或在 `.env` 显式设置 `CPHOS_TEMPLATE_DIR`）。

## 前端静态托管（可选）

如需由本 nginx 同时服务前端单页应用：在 `docker-compose.yml` 的 `nginx` 卷中
挂载前端构建产物到 `/usr/share/nginx/html`，`default.conf` 的 `location /`
已配置 `try_files ... /index.html` 以支持前端路由。
