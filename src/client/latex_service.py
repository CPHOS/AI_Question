"""
远程 LaTeX 编译服务客户端（CPHOS LaTeX Compilation Service）。

把编译卸载到一个独立的 Tectonic 编译服务：本模块负责按 multipart 提交编译作业、
轮询作业状态、下载产物 PDF / 日志、并在取回后释放服务端作业。服务 API 详见
``docs/latex-service-protocol.md``（镜像自服务端 ``docs/API.md``）。

设计要点
========
- **异步作业 + 轮询**：``submit`` 返回 ``job_id``，``wait`` 轮询至终态
  （``succeeded`` / ``failed``）。
- **单作业单文档**：每次提交一个 LaTeX 工程（一个入口 ``.tex`` 加任意相对路径
  的附属文件），服务编译出一份 PDF。多文档（如先图片后正文）由调用方
  （:mod:`app.outputs`）拆成多个作业依次提交。
- **CPHOS 模板内建**：服务镜像预装 CPHOS 模板与字体；``use_cphos_templates``
  控制是否把模板搜索路径加入 Tectonic。
- **鉴权**：除 ``/healthz`` 外所有端点需 API Key，经 ``Authorization: Bearer``
  头发送。
- 仅依赖 :mod:`httpx`。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from config.config import logger

# 作业终态集合：轮询到其一即停止。
_TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
_API_PREFIX = "/v1"
# 单次 HTTP 调用（提交 / 轮询 / 下载）的网络超时，区别于「编译本身」的超时。
_HTTP_TIMEOUT_SECONDS = 60.0


class LatexServiceError(RuntimeError):
    """远程编译服务调用失败（网络错误、非预期状态码、作业级失败等）。"""


class LatexServiceTimeout(LatexServiceError):
    """在客户端总截止时间内作业仍未到达终态。"""


@dataclass
class CompileJob:
    """作业状态快照（POST 受理体与 GET 状态体共用结构）。

    对应服务端 ``CompileResponse`` / ``JobStatusResponse``。受理响应仅含
    ``job_id`` 与 ``status``，其余字段在轮询状态时才会出现。
    """

    job_id: str
    status: str
    entrypoint: str | None = None
    engine: str = ""
    exit_code: int | None = None
    error: str | None = None
    has_pdf: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CompileJob":
        return cls(
            job_id=data["job_id"],
            status=data["status"],
            entrypoint=data.get("entrypoint"),
            engine=str(data.get("engine", "")),
            exit_code=data.get("exit_code"),
            error=data.get("error"),
            has_pdf=bool(data.get("has_pdf", False)),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class LatexServiceClient:
    """远程编译服务的轻量 HTTP 客户端（同步、上下文管理器友好）。"""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        http_timeout: float = _HTTP_TIMEOUT_SECONDS,
    ):
        if not base_url:
            raise LatexServiceError("远程编译服务 base_url 为空")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=http_timeout, headers=headers
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LatexServiceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 基础操作 -------------------------------------------------------

    def submit(
        self,
        files: dict[str, bytes],
        *,
        entrypoint: str | None = None,
        use_cphos_templates: bool = True,
        synctex: bool = False,
        tectonic_args: str | None = None,
    ) -> CompileJob:
        """提交编译作业。

        Args:
            files: 工作区相对路径 → 文件字节内容（入口 tex 源与附属资产）。
                相对路径会作为 multipart 部件文件名，由服务端原样保留。
            entrypoint: 入口 ``.tex`` 的相对路径；省略时服务端自动探测。
            use_cphos_templates: 是否启用 CPHOS 模板搜索路径。
            synctex: 是否保留 SyncTeX 输出。
            tectonic_args: 允许清单内的额外 Tectonic 参数（空格分隔）。

        Returns:
            受理后的作业快照（含 ``job_id`` 与初始 ``status``）。
        """
        multipart = [
            ("files", (rel_path, content, "application/octet-stream"))
            for rel_path, content in files.items()
        ]
        form: dict[str, str] = {
            "use_cphos_templates": "true" if use_cphos_templates else "false",
            "synctex": "true" if synctex else "false",
        }
        if entrypoint:
            form["entrypoint"] = entrypoint
        if tectonic_args:
            form["tectonic_args"] = tectonic_args
        try:
            resp = self._client.post(
                f"{_API_PREFIX}/compile", data=form, files=multipart
            )
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"提交编译作业失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 202:
            raise LatexServiceError(
                f"提交编译作业返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return CompileJob.from_json(resp.json())

    def get(self, job_id: str) -> CompileJob:
        """查询作业状态与元数据。"""
        try:
            resp = self._client.get(f"{_API_PREFIX}/jobs/{quote(job_id, safe='')}")
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"查询作业 {job_id} 失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 404:
            raise LatexServiceError(f"作业不存在: {job_id}")
        if resp.status_code != 200:
            raise LatexServiceError(
                f"查询作业 {job_id} 返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return CompileJob.from_json(resp.json())

    def fetch_result(self, job_id: str, *, fmt: str = "pdf") -> bytes:
        """下载成功作业的构建产物。

        Args:
            job_id: 作业标识。
            fmt: ``pdf``（默认，编译出的 PDF）或 ``bundle``（全部产物的 zip）。
        """
        path = f"{_API_PREFIX}/jobs/{quote(job_id, safe='')}/result"
        try:
            resp = self._client.get(path, params={"format": fmt})
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"下载作业 {job_id} 产物失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 409:
            raise LatexServiceError(f"作业 {job_id} 尚未成功完成，无产物可下载")
        if resp.status_code != 200:
            raise LatexServiceError(
                f"下载作业 {job_id} 产物返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return resp.content

    def fetch_log(self, job_id: str) -> str:
        """下载编译日志（纯文本）；失败时返回空串，不抛异常。"""
        path = f"{_API_PREFIX}/jobs/{quote(job_id, safe='')}/log"
        try:
            resp = self._client.get(path)
        except httpx.HTTPError as exc:
            logger.warning(f"[latex-service] 获取作业 {job_id} 日志失败（忽略）: {exc}")
            return ""
        if resp.status_code != 200:
            return ""
        return resp.text

    def delete(self, job_id: str) -> None:
        """提前释放作业与产物（best-effort）。"""
        try:
            self._client.delete(f"{_API_PREFIX}/jobs/{quote(job_id, safe='')}")
        except httpx.HTTPError as exc:
            logger.warning(f"[latex-service] 释放作业 {job_id} 失败（忽略）: {exc}")

    def version(self) -> dict[str, Any]:
        """查询服务与引擎版本（用于探活 / 诊断）。"""
        try:
            resp = self._client.get(f"{_API_PREFIX}/version")
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"查询服务版本失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise LatexServiceError(
                f"查询服务版本返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return resp.json()

    # ---- 轮询 -----------------------------------------------------------

    def wait(self, job_id: str, *, poll_interval: float, max_wait: float) -> CompileJob:
        """轮询作业至终态；超过 ``max_wait`` 抛 :class:`LatexServiceTimeout`。"""
        deadline = time.monotonic() + max_wait
        while True:
            job = self.get(job_id)
            if job.is_terminal:
                return job
            if time.monotonic() >= deadline:
                raise LatexServiceTimeout(
                    f"等待编译作业 {job_id} 超时（{max_wait}s，末状态={job.status}）"
                )
            time.sleep(poll_interval)


def _safe_body(resp: httpx.Response, limit: int = 500) -> str:
    """截断响应体用于诊断信息，避免日志过长。"""
    try:
        text = resp.text
    except Exception:  # pragma: no cover - 极端编码异常
        return "<unreadable body>"
    return text[:limit]
