"""
统一的任务执行入口。

封装"绑定统计上下文 → 运行状态机 → 采集 API 额度 → 写盘产物"的完整流程，
供 CLI（:mod:`app`）与 API 后端（:mod:`api`）共用，避免逻辑重复。

每次执行通过 :func:`model.stats.run_context` 绑定独立的统计上下文，因此可在
多线程后端中安全并发执行。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from engine.state_machine import build_graph, TaskCancelled
from model.state import WorkflowData
from model.stats import run_context, get_all, get_total_tokens
from app.outputs import write_outputs, budget_snapshot, budget_delta_usd
from config.config import logger

# 进度回调签名，转发给状态机（见 engine.state_machine.PhaseCallback）。
PhaseCallback = Callable[[str, str, WorkflowData], None]
# 取消检查回调，转发给状态机（见 engine.state_machine.CancelCheck）。
CancelCheck = Callable[[], bool]


@dataclass
class RunResult:
    """一次任务执行的结果汇总。"""

    task_id: str
    final_state: WorkflowData
    output_paths: dict[str, Path] = field(default_factory=dict)
    error_msg: str = ""
    elapsed: float = 0.0
    stats: dict[str, dict] = field(default_factory=dict)
    token_usage: dict[str, int] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """执行是否未抛出异常。"""
        return not self.error_msg


def execute_task(
    initial_state: WorkflowData,
    task_id: str,
    output_dir: Path,
    *,
    write: bool = True,
    on_phase: Optional[PhaseCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> RunResult:
    """执行一次完整生成任务。

    Args:
        initial_state: 由 :mod:`spec.normalizer` 规格化得到的初始状态。
        task_id: 任务标识。
        output_dir: 产物落盘目录。
        write: 是否在成功后写盘产物（测试中可关闭）。
        on_phase: 可选的阶段进度回调，透传给状态机用于节点级进度上报。
        should_cancel: 可选的取消检查回调，透传给状态机用于协作式中断。

    Returns:
        :class:`RunResult`，包含最终状态、产物路径、错误信息、耗时与统计。
    """
    with run_context():
        compiled_graph = build_graph()
        budget_start = budget_snapshot()

        logger.info("[runner] 开始执行任务 %s", task_id)
        t_start = time.time()
        error_msg = ""
        cancelled = False
        final_state: WorkflowData = dict(initial_state)

        try:
            final_state = compiled_graph.run(
                initial_state, on_phase=on_phase, should_cancel=should_cancel,
            )
        except TaskCancelled:
            cancelled = True
            logger.info("[runner] 任务 %s 已被取消", task_id)
        except Exception as exc:  # noqa: BLE001 - 汇总为可序列化错误信息
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("[runner] 执行异常: %s", error_msg)

        elapsed = time.time() - t_start
        budget_end = budget_snapshot()
        final_state["api_budget_start"] = budget_start
        final_state["api_budget_end"] = budget_end
        final_state["api_cost_usd"] = budget_delta_usd(budget_start, budget_end)

        output_paths: dict[str, Path] = {}
        if write and not error_msg and not cancelled:
            logger.info("[runner] 推理完成，导出产物...")
            output_paths = write_outputs(task_id, final_state, output_dir)

        return RunResult(
            task_id=task_id,
            final_state=final_state,
            output_paths=output_paths,
            error_msg=error_msg,
            elapsed=elapsed,
            stats=get_all(),
            token_usage=get_total_tokens(),
            cancelled=cancelled,
        )
