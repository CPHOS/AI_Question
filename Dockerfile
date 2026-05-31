# ============================================================================
# CPhOS AI 命题系统 —— 后端镜像
# ----------------------------------------------------------------------------
# 设计要点：
#   * 基于 astral uv 官方镜像（自带 Python 3.11 + uv）。
#   * 应用以「可编辑安装」方式从 /app 源码树运行：config.PROJECT_ROOT 由
#     config.py 的位置上溯三级解析，必须保证代码仍位于源码树内，否则 data/
#     output/ external/ 等相对路径会解析错误。故镜像把整个仓库置于 /app 并在
#     该目录下运行。
#   * 单进程运行（uvicorn 无 --workers）：后台任务执行器与 SSE 注册表均为进程内
#     状态，禁止多 worker / 水平扩缩，否则 MAX_CONCURRENT_JOBS 与进度推送失效。
#   * 本地 LaTeX 工具链（xelatex）体积庞大，默认不安装；容器部署推荐把编译卸载到
#     远程编译服务（LATEX_COMPILER_BACKEND=remote）。如需本机编译，构建时传入
#     --build-arg INSTALL_TEXLIVE=true。
# ============================================================================
ARG UV_IMAGE=ghcr.io/astral-sh/uv:python3.11-bookworm-slim

FROM ${UV_IMAGE}

# 可选：在镜像内安装 TeX Live 以支持 LATEX_COMPILER_BACKEND=local 的本机编译。
ARG INSTALL_TEXLIVE=false

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN if [ "${INSTALL_TEXLIVE}" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            texlive-xetex \
            texlive-latex-extra \
            texlive-fonts-recommended \
            texlive-lang-chinese \
            latexmk && \
        rm -rf /var/lib/apt/lists/*; \
    fi

# ---- 依赖层（仅随 lock/manifest 变化失效，最大化缓存命中）----
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

# ---- 项目层（含源码、prompts、external/FormatChecker 子模块）----
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# 非 root 运行；预创建并授权数据/产物目录（卷挂载点）。
RUN groupadd --system app && \
    useradd --system --gid app --uid 1000 --home-dir /app app && \
    mkdir -p /app/data /app/output && \
    chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:${PATH}" \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

# 健康检查走无鉴权的 /health；端口固定 8000（与 EXPOSE 一致）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else sys.exit(1)"]

# physics-api 控制台脚本 -> uvicorn.run(api.app:app, host=API_HOST, port=API_PORT)
CMD ["physics-api"]
