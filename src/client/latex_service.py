"""
远程 LaTeX 编译服务客户端。

把编译卸载到一个独立的「编译服务」：本模块负责按 multipart 提交编译作业、
轮询作业状态、下载产物 PDF、并在取回后释放服务端作业。协议细节见
``docs/latex-service-protocol.md``。

设计要点
========
- **异步作业 + 轮询**：``submit`` 返回 ``job_id``，``wait`` 轮询至终态。
- **单作业多步骤**：一次提交一个工作区（含子目录）+ 一组有序构建步骤；服务
  串行执行各步（先图片后最终文档），故图片 PDF 对最终文档可见。
- **通用编译器语义**：本客户端不内置 CPHOS 业务逻辑；构建计划由调用方
  （:mod:`app.outputs`）组装。
- 仅依赖 :mod:`httpx`；不引入额外重试/鉴权（协议预留 Authorization 头位）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from config.config import logger

# 作业终态集合：轮询到其一即停止。
_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired"})
_API_PREFIX = "/v1"
# 单次 HTTP 调用（提交 / 轮询 / 下载）的网络超时，区别于「编译本身」的超时。
_HTTP_TIMEOUT_SECONDS = 60.0


class LatexServiceError(RuntimeError):
    """远程编译服务调用失败（网络错误、非预期状态码、作业级失败等）。"""


class LatexServiceTimeout(LatexServiceError):
    """在客户端总截止时间内作业仍未到达终态。"""


@dataclass(frozen=True)
class CompileArtifact:
    """作业产物元信息。``name`` 即工作区相对路径，作为下载端点的标识。"""

    name: str
    media_type: str = "application/octet-stream"
    size: int = 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CompileArtifact":
        return cls(
            name=data["name"],
            media_type=data.get("media_type", "application/octet-stream"),
            size=int(data.get("size", 0)),
        )


@dataclass
class CompileJob:
    """作业状态快照（POST 受理体与 GET 状态体共用结构）。"""

    job_id: str
    status: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[CompileArtifact] = field(default_factory=list)
    error: dict[str, Any] | None = None
    poll_interval: float = 0.0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CompileJob":
        return cls(
            job_id=data["job_id"],
            status=data["status"],
            steps=list(data.get("steps", [])),
            artifacts=[CompileArtifact.from_json(a) for a in data.get("artifacts", [])],
            error=data.get("error"),
            poll_interval=float(data.get("poll_interval_seconds", 0.0) or 0.0),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


class LatexServiceClient:
    """远程编译服务的轻量 HTTP 客户端（同步、上下文管理器友好）。"""

    def __init__(self, base_url: str, *, http_timeout: float = _HTTP_TIMEOUT_SECONDS):
        if not base_url:
            raise LatexServiceError("远程编译服务 base_url 为空")
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=http_timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LatexServiceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 基础操作 -------------------------------------------------------

    def submit(self, files: dict[str, bytes], manifest: dict[str, Any]) -> CompileJob:
        """提交编译作业。

        Args:
            files: 工作区相对路径 → 文件字节内容（tex 源与预编译资产）。
            manifest: 构建计划（engine / timeout_seconds / steps / outputs）。

        Returns:
            受理后的作业快照（含 ``job_id``）。
        """
        multipart = [
            ("file", (rel_path, content, "application/octet-stream"))
            for rel_path, content in files.items()
        ]
        try:
            resp = self._client.post(
                f"{_API_PREFIX}/compile",
                data={"manifest": json.dumps(manifest, ensure_ascii=False)},
                files=multipart,
            )
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"提交编译作业失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 202:
            raise LatexServiceError(
                f"提交编译作业返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return CompileJob.from_json(resp.json())

    def get(self, job_id: str) -> CompileJob:
        """查询作业状态。"""
        try:
            resp = self._client.get(f"{_API_PREFIX}/compile/{quote(job_id, safe='')}")
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"查询作业 {job_id} 失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 404:
            raise LatexServiceError(f"作业不存在: {job_id}")
        if resp.status_code == 410:
            return CompileJob(job_id=job_id, status="expired")
        if resp.status_code != 200:
            raise LatexServiceError(
                f"查询作业 {job_id} 返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return CompileJob.from_json(resp.json())

    def fetch_artifact(self, job_id: str, name: str) -> bytes:
        """下载单个产物（按工作区相对路径标识）。"""
        path = f"{_API_PREFIX}/compile/{quote(job_id, safe='')}/artifacts/{quote(name, safe='')}"
        try:
            resp = self._client.get(path)
        except httpx.HTTPError as exc:
            raise LatexServiceError(f"下载产物 {name} 失败: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise LatexServiceError(
                f"下载产物 {name} 返回非预期状态 {resp.status_code}: {_safe_body(resp)}"
            )
        return resp.content

    def delete(self, job_id: str) -> None:
        """提前释放作业与产物（best-effort）。"""
        try:
            self._client.delete(f"{_API_PREFIX}/compile/{quote(job_id, safe='')}")
        except httpx.HTTPError as exc:
            logger.warning(f"[latex-service] 释放作业 {job_id} 失败（忽略）: {exc}")

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
            # 服务可通过 poll_interval_seconds 给出建议节流；取与配置的较大值。
            time.sleep(max(poll_interval, job.poll_interval))


def _safe_body(resp: httpx.Response, limit: int = 500) -> str:
    """截断响应体用于诊断信息，避免日志过长。"""
    try:
        text = resp.text
    except Exception:  # pragma: no cover - 极端编码异常
        return "<unreadable body>"
    return text[:limit]
