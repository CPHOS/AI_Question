import httpx
import pytest

from client.latex_service import (
    CompileJob,
    LatexServiceClient,
    LatexServiceError,
    LatexServiceTimeout,
)


def _make_client(handler, *, api_key: str = "") -> LatexServiceClient:
    """构造一个使用 MockTransport 的客户端，避免真实网络。"""
    client = LatexServiceClient("http://latex.test", api_key=api_key)
    client._client = httpx.Client(
        base_url="http://latex.test",
        transport=httpx.MockTransport(handler),
        headers=({"Authorization": f"Bearer {api_key}"} if api_key else {}),
    )
    return client


def test_submit_poll_fetch_happy_path(monkeypatch):
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/compile":
            # 校验 multipart 字段名 files、入口文件、表单字段
            assert b'name="files"' in request.content
            assert b"task.tex" in request.content
            assert b'name="entrypoint"' in request.content
            assert b'name="use_cphos_templates"' in request.content
            return httpx.Response(202, json={"job_id": "job1", "status": "queued"})
        if request.method == "GET" and request.url.path == "/v1/jobs/job1":
            state["polls"] += 1
            if state["polls"] < 2:
                return httpx.Response(200, json={"job_id": "job1", "status": "running"})
            return httpx.Response(200, json={
                "job_id": "job1",
                "status": "succeeded",
                "engine": "Tectonic 0.15.0",
                "exit_code": 0,
                "has_pdf": True,
            })
        if request.url.path == "/v1/jobs/job1/result":
            assert request.url.params.get("format") == "pdf"
            return httpx.Response(200, content=b"%PDF-1.4\n")
        raise AssertionError(f"unexpected {request.method} {request.url}")

    monkeypatch.setattr("client.latex_service.time.sleep", lambda *_: None)
    with _make_client(handler) as client:
        job = client.submit(
            {"task.tex": b"\\documentclass{article}"},
            entrypoint="task.tex",
            use_cphos_templates=True,
        )
        assert job.job_id == "job1"
        job = client.wait("job1", poll_interval=0.0, max_wait=10)
        assert job.status == "succeeded"
        assert job.succeeded is True
        assert job.has_pdf is True
        data = client.fetch_result("job1")
        assert data.startswith(b"%PDF")


def test_submit_sends_authorization_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(202, json={"job_id": "j", "status": "queued"})

    with _make_client(handler, api_key="secret-key") as client:
        client.submit({"a.tex": b"x"}, entrypoint="a.tex")
    assert seen["auth"] == "Bearer secret-key"


def test_wait_returns_failed_job(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "job_id": "j", "status": "failed", "error": "compile error", "exit_code": 1,
        })

    monkeypatch.setattr("client.latex_service.time.sleep", lambda *_: None)
    with _make_client(handler) as client:
        job = client.wait("j", poll_interval=0.0, max_wait=10)
        assert job.status == "failed"
        assert job.succeeded is False
        assert job.error == "compile error"


def test_get_treats_404_as_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _make_client(handler) as client:
        with pytest.raises(LatexServiceError):
            client.get("gone")


def test_fetch_result_409_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="not finished")

    with _make_client(handler) as client:
        with pytest.raises(LatexServiceError):
            client.fetch_result("j")


def test_fetch_log_returns_text_and_swallows_errors():
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="! LaTeX Error")

    with _make_client(ok_handler) as client:
        assert client.fetch_log("j") == "! LaTeX Error"

    def missing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _make_client(missing_handler) as client:
        assert client.fetch_log("j") == ""


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
            client.submit({"a.tex": b"x"}, entrypoint="a.tex")


def test_empty_base_url_raises():
    with pytest.raises(LatexServiceError):
        LatexServiceClient("")


def test_compile_job_is_terminal():
    assert CompileJob("j", "succeeded").is_terminal
    assert CompileJob("j", "failed").is_terminal
    assert not CompileJob("j", "queued").is_terminal
    assert not CompileJob("j", "running").is_terminal
