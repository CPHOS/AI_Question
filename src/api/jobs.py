"""
后台任务执行器。

用线程池并发执行生成任务（数量受 ``MAX_CONCURRENT_JOBS`` 限制），并把生命周期
状态（``queued`` → ``running`` → ``done`` / ``error`` / ``aborted``）落库。每个任务的
产物写入 ``OUTPUT_DIR/{user_id}/``，与其他用户隔离。

并发安全
========
:func:`app.runner.execute_task` 内部用 :func:`model.stats.run_context` 为每次执行
绑定独立的统计上下文，因此多个任务在不同线程并发执行时统计互不串扰。
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from api import store
from api import progress
from app.runner import execute_task
from spec.normalizer import from_api
from config.config import OUTPUT_DIR, MAX_CONCURRENT_JOBS, logger

_executor: ThreadPoolExecutor | None = None


def start_executor() -> None:
    """初始化线程池（应用启动时调用）。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, MAX_CONCURRENT_JOBS),
            thread_name_prefix="genjob",
        )
        logger.info("[jobs] 执行器启动，max_workers=%d", max(1, MAX_CONCURRENT_JOBS))


def shutdown_executor() -> None:
    """关闭线程池（应用退出时调用）。"""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
        logger.info("[jobs] 执行器已关闭")


def user_output_dir(user_id: str) -> Path:
    """返回某用户的产物目录。"""
    return OUTPUT_DIR / user_id


def submit_task(
    user_id: str,
    *,
    topic: str,
    source_material: str,
    difficulty: str,
    total_score: int,
    mode: str | None,
) -> str:
    """登记并提交一个生成任务，返回 task_id。"""
    if _executor is None:
        raise RuntimeError("任务执行器未启动")

    task_id = f"task_{uuid.uuid4().hex[:8]}"
    initial_state = from_api(
        topic=topic,
        source_material=source_material,
        difficulty=difficulty,
        total_score=total_score,
        mode=mode,
    )
    store.create_task(
        task_id=task_id,
        user_id=user_id,
        mode=initial_state.get("mode", ""),
        topic=initial_state.get("topic", ""),
        total_score=initial_state.get("total_score", 0),
    )
    _executor.submit(_run_job, task_id, user_id, initial_state)
    logger.info("[jobs] 任务已提交 task_id=%s user=%s", task_id, user_id)
    return task_id


def _make_progress_sink(task_id: str):
    """构造一个把阶段事件落库的进度回调（闭包持有单调序号）。

    回调内部完全容错：任何持久化异常都被吞掉并记日志，绝不冒泡到状态机
    （状态机侧也有一层兜底），保证进度上报失败不影响生成主流程。
    """
    counter = {"seq": 0}

    def sink(phase: str, status: str, data: dict[str, Any]) -> None:
        counter["seq"] += 1
        seq = counter["seq"]
        try:
            output = progress.format_phase_output(phase, data) if status == "completed" else None
            store.append_event(task_id, seq, phase, status, output)
            store.set_task_phase(task_id, phase)
        except Exception:  # noqa: BLE001 - 进度落库失败不影响任务
            logger.warning("[jobs] 进度落库失败 task_id=%s phase=%s", task_id, phase, exc_info=True)

    return sink


def _run_job(task_id: str, user_id: str, initial_state: dict[str, Any]) -> None:
    """线程池工作函数：执行任务并落库结果。"""
    store.set_task_status(task_id, "running")
    try:
        result = execute_task(
            initial_state, task_id, user_output_dir(user_id),
            on_phase=_make_progress_sink(task_id),
        )
    except Exception as exc:  # noqa: BLE001 - 兜底，保证状态被更新
        logger.exception("[jobs] 任务异常 task_id=%s", task_id)
        store.finish_task(task_id, "error", summary={}, error=f"{type(exc).__name__}: {exc}")
        return

    final = result.final_state
    decision = final.get("arbiter_decision", "")
    if result.error_msg:
        status = "error"
    elif decision == "ABORT":
        status = "aborted"
    else:
        status = "done"

    summary = _build_summary(result, user_id)
    store.finish_task(task_id, status, summary=summary, error=result.error_msg)
    logger.info("[jobs] 任务完成 task_id=%s status=%s", task_id, status)


def _build_summary(result, user_id: str) -> dict[str, Any]:
    """从 RunResult 抽取可序列化的摘要。"""
    final = result.final_state
    artifacts = list_artifacts(user_id, result.task_id)
    return {
        "arbiter_decision": final.get("arbiter_decision", ""),
        "error_category": final.get("error_category", ""),
        "retry_count": final.get("retry_count", 0),
        "problem_retry_count": final.get("problem_retry_count", 0),
        "solution_retry_count": final.get("solution_retry_count", 0),
        "block_formula_count": len(final.get("formula_dict", {})),
        "inline_formula_count": len(final.get("inline_dict", {})),
        "figure_count": len(final.get("figure_descriptions", {})),
        "token_usage": result.token_usage,
        "api_cost_usd": final.get("api_cost_usd"),
        "elapsed": round(result.elapsed, 2),
        "artifacts": [a["name"] for a in artifacts],
    }


# ============ 产物枚举与定位 ============

_ARTIFACT_SUFFIXES = {
    "_final.tex": "final_latex",
    "_final.pdf": "final_pdf",
    "_draft.md": "draft",
    "_tagged.md": "tagged",
    "_log.json": "log",
    "_report.md": "report",
}


def list_artifacts(user_id: str, task_id: str) -> list[dict[str, Any]]:
    """枚举某任务在用户目录下的产物文件（含 assets 子目录）。"""
    base = user_output_dir(user_id)
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for suffix, name in _ARTIFACT_SUFFIXES.items():
        p = base / f"{task_id}{suffix}"
        if p.exists():
            items.append({"name": name, "filename": p.name, "size": p.stat().st_size})
    assets = base / f"{task_id}_assets"
    if assets.is_dir():
        for f in sorted(assets.iterdir()):
            if f.is_file():
                items.append({
                    "name": f"assets/{f.name}",
                    "filename": f"{task_id}_assets/{f.name}",
                    "size": f.stat().st_size,
                })
    return items


def resolve_artifact(user_id: str, task_id: str, name: str) -> Path | None:
    """把产物逻辑名解析为磁盘路径，并做归属与穿越校验。"""
    base = user_output_dir(user_id).resolve()
    for item in list_artifacts(user_id, task_id):
        if item["name"] == name:
            candidate = (base / item["filename"]).resolve()
            # 防目录穿越：解析后的路径必须仍位于用户目录内。
            if base in candidate.parents or candidate.parent == base:
                return candidate
    return None
