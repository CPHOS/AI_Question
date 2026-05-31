"""API 测试共享夹具。

为每个用例提供隔离的 SQLite 数据库与产物目录，并把真实的生成执行替换为
轻量假实现，避免触发 LLM 调用。
"""
import pytest


@pytest.fixture(autouse=True)
def _disable_format_check(monkeypatch):
    """默认关闭外部 FormatChecker，避免单测触发子进程与外部 submodule 依赖。

    需要验证集成的用例可显式 ``monkeypatch.setattr(cfg, "FORMAT_CHECK_ENABLED", True)``。
    """
    from config import config as cfg

    monkeypatch.setattr(cfg, "FORMAT_CHECK_ENABLED", False)


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    """把数据库切到临时文件并初始化，用例结束后复位。"""
    from api import db

    db_path = tmp_path / "api.db"
    db.configure(db_path)
    db.init_db()
    try:
        yield db
    finally:
        db.configure(db_path)  # 关闭连接
        # 复位为模块默认，避免影响其他用例
        from config.config import DB_PATH
        db.configure(DB_PATH)


@pytest.fixture
def fake_execute(monkeypatch):
    """用假执行替换 app.runner.execute_task（jobs 内引用）。"""
    from app.runner import RunResult

    def _fake(initial_state, task_id, output_dir, write=True, on_phase=None, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        final_state = dict(initial_state)
        final_state.update({
            "arbiter_decision": "PASS",
            "title": "假题目",
            "planning_notes": "规划要点",
            "problem_text": "题干内容",
            "solution_text": "参考答案",
            "math_review": "数学审核意见",
            "physics_review": "物理审核意见",
            "structure_review": "",
            "quality_review": "质量审核意见",
            "arbiter_reason": "通过理由",
            "arbiter_feedback": "",
            "error_category": "none",
            "final_latex": "\\documentclass[answer]{cphos}\n% fake\n",
            "formula_dict": {},
            "inline_dict": {},
            "figure_descriptions": {},
            "api_cost_usd": 0.0,
        })
        # 模拟若干阶段事件，便于测试进度端点。
        if on_phase is not None:
            for phase in ("PLANNING", "PROBLEM_GENERATING", "REVIEWING",
                          "ARBITRATING", "FORMATTING", "DONE"):
                on_phase(phase, "running", final_state)
                on_phase(phase, "completed", final_state)
        paths = {}
        if write:
            tex = output_dir / f"{task_id}_final.tex"
            tex.write_text(final_state["final_latex"], encoding="utf-8")
            report = output_dir / f"{task_id}_report.md"
            report.write_text("# 仲裁报告\n\nPASS\n", encoding="utf-8")
            paths = {"final_latex": tex, "report": report}
        return RunResult(
            task_id=task_id,
            final_state=final_state,
            output_paths=paths,
            error_msg="",
            elapsed=0.01,
            stats={},
            token_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr("api.jobs.execute_task", _fake)
    return _fake


@pytest.fixture
def client(tmp_path, monkeypatch, api_db, fake_execute):
    """构造已初始化的 FastAPI TestClient，含引导管理员 token。"""
    from fastapi.testclient import TestClient
    import api.app as api_app

    monkeypatch.setattr(api_app, "ADMIN_BOOTSTRAP_TOKEN", "admin-test-token")
    monkeypatch.setattr("api.jobs.OUTPUT_DIR", tmp_path / "out")

    with TestClient(api_app.app) as c:
        c.admin_token = "admin-test-token"  # type: ignore[attr-defined]
        yield c
