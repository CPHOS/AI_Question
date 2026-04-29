"""
全局工作流状态机。

参照 AI_Reviewer 状态机模式，管理生成工作流的阶段流转：
  INIT → PLANNING → PROBLEM_GENERATING → SOLUTION_GENERATING
       → REVIEWING → ARBITRATING
       → FORMATTING → TEMPLATE_FIXING → DONE

仲裁路由：
  PASS            → 进入后处理
  RETRY_PROBLEM   → 回到 PROBLEM_GENERATING
  RETRY_SOLUTION  → 回到 SOLUTION_GENERATING（保留题干）
  ABORT           → ABORTED

任意阶段异常 → ERROR
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto

from model.state import WorkflowData
from config.config import MAX_RETRY_COUNT, logger


class Phase(Enum):
    """工作流阶段。"""
    INIT = auto()
    PLANNING = auto()
    PROBLEM_GENERATING = auto()
    SOLUTION_GENERATING = auto()
    REVIEWING = auto()
    ARBITRATING = auto()
    FORMATTING = auto()
    TEMPLATE_FIXING = auto()
    DONE = auto()
    ABORTED = auto()
    ERROR = auto()


# 合法仲裁决策
_VALID_DECISIONS = ("PASS", "RETRY_PROBLEM", "RETRY_SOLUTION", "ABORT")


class GenerationStateMachine:
    """驱动命题生成工作流的状态机。"""

    def __init__(self):
        self._phase: Phase = Phase.INIT
        self._start_time: float = 0.0

    @property
    def phase(self) -> Phase:
        return self._phase

    def _transition(self, to: Phase) -> None:
        """执行阶段转移并记录日志。"""
        logger.info("阶段转移: %s → %s", self._phase.name, to.name)
        self._phase = to

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route(self, data: WorkflowData) -> str:
        """仲裁后的条件路由。

        采用分阶段计数：RETRY_PROBLEM 和 RETRY_SOLUTION 的上限独立统计，
        命中当前阶段上限时才熔断。另外 `retry_count`（总重试次数）
        仍设 2×MAX_RETRY_COUNT 的硬上限兜底，避免两阶段交替震荡。

        日志策略（保留单一入口/出口日志，去除分支末尾重复打印）：
          - 进入路由：一次 INFO，统一打印 decision + 三个计数器 + error_category
          - 离开路由：一次 INFO/WARNING，按返回值分级（pass/pass_with_edits = INFO，
            abort/end = WARNING，retry_* = INFO）

        返回值: "pass" | "pass_with_edits" | "retry_problem" | "retry_solution" | "abort" | "end"
        """
        decision = data.get("arbiter_decision", "RETRY_PROBLEM")
        prob_retry = data.get("problem_retry_count", 0)
        sol_retry = data.get("solution_retry_count", 0)
        total_retry = data.get("retry_count", prob_retry + sol_retry)
        error_cat = data.get("error_category", "fatal")

        # 入口日志：一行覆盖全部决策上下文
        logger.info(
            "[router] decision=%s | error_category=%s | retry: problem=%d/%d solution=%d/%d total=%d/%d",
            decision, error_cat,
            prob_retry, MAX_RETRY_COUNT,
            sol_retry, MAX_RETRY_COUNT,
            total_retry, 2 * MAX_RETRY_COUNT,
        )

        if decision == "PASS":
            logger.info("[router] → pass（进入后处理流水线）")
            return "pass"

        if decision == "ABORT":
            logger.warning("[router] → abort（仲裁判定 ABORT，流程终止）")
            return "abort"

        # 当前阶段是否已达上限
        stage_exhausted = (
            (decision == "RETRY_PROBLEM" and prob_retry >= MAX_RETRY_COUNT)
            or (decision == "RETRY_SOLUTION" and sol_retry >= MAX_RETRY_COUNT)
        )
        # 总次数硬熔断，防止两阶段交替重试无限循环
        total_exhausted = total_retry >= 2 * MAX_RETRY_COUNT

        if stage_exhausted or total_exhausted:
            cap_reason = (
                "stage_exhausted" if stage_exhausted and not total_exhausted
                else "total_exhausted" if total_exhausted and not stage_exhausted
                else "stage+total_exhausted"
            )
            if error_cat == "style":
                logger.info(
                    "[router] → pass_with_edits（达上限 [%s] 但 error_category=style，按"
                    "有条件通过处理）",
                    cap_reason,
                )
                return "pass_with_edits"
            logger.warning(
                "[router] → end（达上限 [%s]，error_category=%s，强制终止）",
                cap_reason, error_cat,
            )
            return "end"

        if decision == "RETRY_SOLUTION":
            logger.info("[router] → retry_solution（回到解题生成）")
            return "retry_solution"

        # RETRY_PROBLEM；未识别值理论上已在仲裁 Agent 内被收敛到 RETRY_PROBLEM
        if decision != "RETRY_PROBLEM":
            logger.warning(
                "[router] 未识别 decision=%r，按 RETRY_PROBLEM 兜底", decision,
            )
        logger.info("[router] → retry_problem（回到命题生成）")
        return "retry_problem"

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self, data: WorkflowData) -> WorkflowData:
        """驱动完整的生成工作流。"""
        from spec.planner import run_planning
        from agents.problem_generator import problem_generator_agent
        from agents.solution_generator import solution_generator_agent
        from agents.reviewers import run_reviews
        from agents.arbiter import arbiter_agent
        from latex.isolate import isolate
        from latex.format import formatting_agent
        from latex.merge import merge
        from latex.template_agent import fix_template

        self._start_time = time.monotonic()
        data = dict(data)  # 允许修改

        try:
            # ===== 命题规划 =====
            self._transition(Phase.PLANNING)
            data.update(run_planning(data))

            need_problem = True

            while True:
                # ===== 命题生成 =====
                if need_problem:
                    self._transition(Phase.PROBLEM_GENERATING)
                    data.update(problem_generator_agent(data))

                # ===== 解题生成 =====
                self._transition(Phase.SOLUTION_GENERATING)
                data.update(solution_generator_agent(data))

                # 合并 problem_text + solution_text 供审核
                data["draft_content"] = (
                    data.get("problem_text", "")
                    + "\n\n参考答案\n\n"
                    + data.get("solution_text", "")
                )

                # ===== 并行审核 =====
                self._transition(Phase.REVIEWING)
                data.update(run_reviews(data))

                # ===== 仲裁 =====
                self._transition(Phase.ARBITRATING)
                data.update(arbiter_agent(data))

                # ===== 路由 =====
                route = self._route(data)

                if route in ("pass", "pass_with_edits"):
                    if route == "pass_with_edits":
                        data["arbiter_decision"] = "PASS_WITH_EDITS"
                    break
                elif route == "abort":
                    self._transition(Phase.ABORTED)
                    return data
                elif route == "end":
                    return data
                elif route == "retry_solution":
                    need_problem = False
                else:
                    # retry_problem
                    need_problem = True

            # ===== LaTeX 后处理流水线 =====
            self._transition(Phase.FORMATTING)
            data.update(isolate(data))
            data.update(formatting_agent(data))
            data.update(merge(data))

            # ===== 模板修正 =====
            self._transition(Phase.TEMPLATE_FIXING)
            data.update(fix_template(data))

            self._transition(Phase.DONE)
            elapsed = time.monotonic() - self._start_time
            logger.info("生成完成，总耗时 %.2f 秒", elapsed)
            return data

        except Exception:
            self._transition(Phase.ERROR)
            elapsed = time.monotonic() - self._start_time
            logger.exception("生成流程出错 (已耗时 %.2f 秒)", elapsed)
            raise

def build_graph() -> GenerationStateMachine:
    """构建并返回工作流状态机。"""
    logger.info("[workflow] 工作流状态机构建完成")
    return GenerationStateMachine()
