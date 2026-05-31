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

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any

from api import store
from api import progress
from app.runner import execute_task
from app.outputs import ARTIFACT_SUFFIXES, ASSETS_DIR_SUFFIX, recompile_outputs
from spec.normalizer import from_api
from config.config import OUTPUT_DIR, MAX_CONCURRENT_JOBS, logger

_executor: ThreadPoolExecutor | None = None

# 取消协作：task_id → Event（置位表示已请求取消）；并保留 Future 以便取消排队中的任务。
_cancels: dict[str, threading.Event] = {}
_futures: dict[str, Future] = {}
_registry_lock = threading.Lock()

# 按需编译串行化：每任务一把锁，防止并发重编同一任务产物互相覆盖。
_compile_locks: dict[str, threading.Lock] = {}
_compile_registry_lock = threading.Lock()


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


def executor_running() -> bool:
    """返回后台任务执行器是否处于运行态（供健康检查使用）。"""
    return _executor is not None


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
        difficulty=initial_state.get("difficulty", ""),
        source_material=initial_state.get("source_material", ""),
    )
    event = threading.Event()
    with _registry_lock:
        _cancels[task_id] = event
    future = _executor.submit(_run_job, task_id, user_id, initial_state)
    with _registry_lock:
        _futures[task_id] = future
    logger.info("[jobs] 任务已提交 task_id=%s user=%s", task_id, user_id)
    return task_id


def _discard_registry(task_id: str) -> None:
    """清理某任务的取消事件与 Future 引用（任务终结时调用）。"""
    with _registry_lock:
        _cancels.pop(task_id, None)
        _futures.pop(task_id, None)


def cancel_task(task_id: str) -> str:
    """协作式取消一个任务。

    - 若任务仍在队列中未启动，``Future.cancel()`` 成功 → 直接标记 ``aborted``；
    - 若任务正在运行，置位取消事件并把状态置为 ``aborting``，工作流会在下一个
      阶段边界停止并最终落为 ``aborted``。

    Returns:
        ``"aborted"``（已立即终止）或 ``"aborting"``（正在停止）。
    """
    with _registry_lock:
        event = _cancels.get(task_id)
        future = _futures.get(task_id)
    if event is not None:
        event.set()
    if future is not None and future.cancel():
        # 任务尚未开始执行，已从队列移除。
        store.finish_task(task_id, "aborted", summary={}, error="")
        _discard_registry(task_id)
        logger.info("[jobs] 任务在排队中被取消 task_id=%s", task_id)
        return "aborted"
    store.set_task_status(task_id, "aborting")
    logger.info("[jobs] 已请求取消运行中任务 task_id=%s", task_id)
    return "aborting"


def _make_progress_sink(task_id: str):
    """构造一个把阶段事件落库的进度回调（闭包持有单调序号）。

    回调内部完全容错：任何持久化异常都被吞掉并记日志，绝不冒泡到状态机
    （状态机侧也有一层兜底），保证进度上报失败不影响生成主流程。

    为前端时间线提供两个稳定标识：
      - ``occurrence_id``：同一阶段同一次执行的标识（``running`` 进入时自增该阶段
        计数，随后的 ``completed`` 复用同一值），形如 ``"REVIEWING#2"``，使前端无需
        依赖「running 后紧跟同 phase completed」的顺序假设即可配对。
      - ``round``：重试轮次（从 1 起），由状态机的总重试计数 ``retry_count`` 推导。
    """
    counter = {"seq": 0}
    occurrences: dict[str, int] = {}

    def sink(phase: str, status: str, data: dict[str, Any]) -> None:
        counter["seq"] += 1
        seq = counter["seq"]
        # running 进入阶段时开启新一次 occurrence；completed 复用当前计数。
        if status == "running":
            occurrences[phase] = occurrences.get(phase, 0) + 1
        occurrence_id = f"{phase}#{occurrences.get(phase, 1)}"
        total_retry = data.get(
            "retry_count",
            data.get("problem_retry_count", 0) + data.get("solution_retry_count", 0),
        )
        round_ = int(total_retry) + 1
        try:
            output = progress.format_phase_output(phase, data) if status == "completed" else None
            store.append_event(
                task_id, seq, phase, status, output,
                occurrence_id=occurrence_id, round_=round_,
            )
            store.set_task_phase(task_id, phase)
        except Exception:  # noqa: BLE001 - 进度落库失败不影响任务
            logger.warning("[jobs] 进度落库失败 task_id=%s phase=%s", task_id, phase, exc_info=True)

    return sink


def _run_job(task_id: str, user_id: str, initial_state: dict[str, Any]) -> None:
    """线程池工作函数：执行任务并落库结果。"""
    with _registry_lock:
        event = _cancels.get(task_id)
    # 启动前已请求取消：直接落为 aborted，不进入生成流程。
    if event is not None and event.is_set():
        store.finish_task(task_id, "aborted", summary={}, error="")
        _discard_registry(task_id)
        logger.info("[jobs] 任务启动前已取消 task_id=%s", task_id)
        return

    store.set_task_status(task_id, "running")
    try:
        result = execute_task(
            initial_state, task_id, user_output_dir(user_id),
            on_phase=_make_progress_sink(task_id),
            should_cancel=(event.is_set if event is not None else None),
        )
    except Exception as exc:  # noqa: BLE001 - 兜底，保证状态被更新
        logger.exception("[jobs] 任务异常 task_id=%s", task_id)
        store.finish_task(task_id, "error", summary={}, error=f"{type(exc).__name__}: {exc}")
        _discard_registry(task_id)
        return

    if result.cancelled:
        store.finish_task(task_id, "aborted", summary={}, error="")
        _discard_registry(task_id)
        logger.info("[jobs] 任务完成 task_id=%s status=aborted", task_id)
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
    _discard_registry(task_id)
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

# 后缀 → 逻辑名：反转 app.outputs 的权威映射，确保写盘与枚举两端一致。
_ARTIFACT_SUFFIXES = {suffix: name for name, suffix in ARTIFACT_SUFFIXES.items()}


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
    assets = base / f"{task_id}{ASSETS_DIR_SUFFIX}"
    if assets.is_dir():
        for f in sorted(assets.iterdir()):
            if f.is_file():
                items.append({
                    "name": f"assets/{f.name}",
                    "filename": f"{task_id}{ASSETS_DIR_SUFFIX}/{f.name}",
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


# ============ 编译状态与按需编译 ============

def read_compile_status(user_id: str, task_id: str) -> dict[str, Any]:
    """从 ``_log.json`` 读取编译状态，供 API 暴露（无日志或解析失败返回空状态）。

    Returns:
        ``{"latex_compile_status": {...}, "figure_compile_status": {...}}``。
    """
    empty = {"latex_compile_status": {}, "figure_compile_status": {}}
    log_path = resolve_artifact(user_id, task_id, "log")
    if log_path is None or not log_path.exists():
        return empty
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    return {
        "latex_compile_status": data.get("latex_compile_status", {}) or {},
        "figure_compile_status": data.get("figure_compile_status", {}) or {},
    }


def _compile_lock_for(task_id: str) -> threading.Lock:
    """获取（或惰性创建）某任务的编译锁。"""
    with _compile_registry_lock:
        lock = _compile_locks.get(task_id)
        if lock is None:
            lock = threading.Lock()
            _compile_locks[task_id] = lock
        return lock


def discard_compile_lock(task_id: str) -> None:
    """删除某任务的编译锁，避免注册表随任务删除而无限增长。

    仅在当前无编译进行（锁空闲）时移除；若正有编译在进行则跳过，
    交由下次删除时清理。应在任务被删除后调用。
    """
    with _compile_registry_lock:
        lock = _compile_locks.get(task_id)
        if lock is None:
            return
        if lock.acquire(blocking=False):
            try:
                del _compile_locks[task_id]
            finally:
                lock.release()


def recompile_task(user_id: str, task_id: str) -> dict[str, Any]:
    """按需重新编译任务的最终 LaTeX 与图片（不调用 LLM），串行化执行。

    Returns:
        编译状态字典（同 :func:`read_compile_status` 的结构）。

    Raises:
        FileNotFoundError: 无 ``final_latex`` 产物，无可编译对象。
        RuntimeError: 已有一次编译在进行中（未取得锁）。
    """
    lock = _compile_lock_for(task_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("该任务正在编译中，请稍后重试")
    try:
        logger.info("[jobs] 按需编译开始 task_id=%s user=%s", task_id, user_id)
        status = recompile_outputs(task_id, user_output_dir(user_id))
        logger.info(
            "[jobs] 按需编译完成 task_id=%s ok=%s",
            task_id, status["latex_compile_status"].get("ok"),
        )
        return status
    finally:
        lock.release()

