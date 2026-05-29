import json

import httpx
import pytest

from client.latex_service import (
    CompileJob,
    LatexServiceClient,
    LatexServiceError,
    LatexServiceTimeout,
)


def _make_client(handler) -> LatexServiceClient:
    """构造一个使用 MockTransport 的客户端，避免真实网络。"""
    client = LatexServiceClient("http://latex.test")
    client._client = httpx.Client(
        base_url="http://latex.test", transport=httpx.MockTransport(handler)
    )
    return client


def test_submit_poll_fetch_happy_path(monkeypatch):
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/compile":
            # 校验 multipart 含 manifest 与文件部件
            assert b"manifest" in request.content
            assert b"task.tex" in request.content
            return httpx.Response(202, json={"job_id": "job1", "status": "queued"})
        if request.method == "GET" and request.url.path == "/v1/compile/job1":
            state["polls"] += 1
            if state["polls"] < 2:
                return httpx.Response(200, json={"job_id": "job1", "status": "running"})
            return httpx.Response(200, json={
                "job_id": "job1",
                "status": "completed",
                "steps": [{"id": "final", "ok": True, "produced": ["task.pdf"]}],
                "artifacts": [{"name": "task.pdf", "media_type": "application/pdf", "size": 9}],
            })
        if request.url.path == "/v1/compile/job1/artifacts/task.pdf":
            return httpx.Response(200, content=b"%PDF-1.4\n")
        raise AssertionError(f"unexpected {request.method} {request.url}")

    monkeypatch.setattr("client.latex_service.time.sleep", lambda *_: None)
    with _make_client(handler) as client:
        job = client.submit({"task.tex": b"\\documentclass{article}"}, {"engine": "xelatex"})
        assert job.job_id == "job1"
        job = client.wait("job1", poll_interval=0.0, max_wait=10)
        assert job.status == "completed"
        assert job.steps[0]["ok"] is True
        data = client.fetch_artifact("job1", "task.pdf")
        assert data.startswith(b"%PDF")


def test_wait_returns_failed_job(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "job_id": "j", "status": "failed", "error": {"message": "no engine"},
        })

    monkeypatch.setattr("client.latex_service.time.sleep", lambda *_: None)
    with _make_client(handler) as client:
        job = client.wait("j", poll_interval=0.0, max_wait=10)
        assert job.status == "failed"
        assert job.error["message"] == "no engine"


def test_get_treats_410_as_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410)

    with _make_client(handler) as client:
        job = client.get("gone")
        assert job.status == "expired"


def test_wait_times_out(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": "j", "status": "running"})

    monkeypatch.setattr("client.latex_service.time.sleep", lambda *_: None)
    # 让 deadline 立刻过期：max_wait=0 → 第一次轮询后即超时
    with _make_client(handler) as client:
        with pytest.raises(LatexServiceTimeout):
            client.wait("j", poll_interval=0.0, max_wait=0)


def test_submit_non_202_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _make_client(handler) as client:
        with pytest.raises(LatexServiceError):
            client.submit({"a.tex": b"x"}, {})


def test_empty_base_url_raises():
    with pytest.raises(LatexServiceError):
        LatexServiceClient("")


def test_compile_job_is_terminal():
    assert CompileJob("j", "completed").is_terminal
    assert CompileJob("j", "failed").is_terminal
    assert CompileJob("j", "expired").is_terminal
    assert not CompileJob("j", "running").is_terminal
