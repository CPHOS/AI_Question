"""
纯 Python 状态机编排（替代 LangGraph）。

流程:
  START → generator_agent → [math_verifier, physics_verifier] (并行)
        → arbiter_agent → PASS/RETRY/ABORT 路由
        → (PASS) python_parser → formatting_agent → python_merger → END
"""
from concurrent.futures import ThreadPoolExecutor

from model.state import AgentState
from generator.generator import generator_agent
from generator.math_verifier import math_verifier
from generator.physics_verifier import physics_verifier
from generator.arbiter import arbiter_agent
from formatter.parser import python_parser
from formatter.formatter import formatting_agent
from formatter.merger import python_merger
from config.settings import MAX_RETRY_COUNT, logger


def _arbiter_router(state: AgentState) -> str:
    """
    仲裁后的条件路由。
    返回值: "pass" | "pass_with_edits" | "retry" | "end"
    """
    decision = state.get("arbiter_decision", "RETRY")
    retry = state.get("retry_count", 0)
    error_category = state.get("error_category", "fatal")

    if decision == "PASS":
        logger.info("[router] PASS → 进入后处理流水线")
        return "pass"

    if decision == "ABORT":
        logger.warning("[router] 仲裁判定 ABORT → 流程终止")
        return "end"

    # decision == "RETRY"
    if retry >= MAX_RETRY_COUNT:
        if error_category == "style":
            logger.info("[router] 重试上限但仅有用语规范问题 → 视为通过（需人工修订）")
            return "pass_with_edits"
        logger.warning(f"[router] 达到最大重试 {MAX_RETRY_COUNT} 次 → 强制终止")
        return "end"

    logger.info(f"[router] RETRY → 回到命题节点 (已重试 {retry} 次)")
    return "retry"


class CompiledWorkflow:
    """
    编译后的工作流对象，提供 .invoke(state, config=...) 接口。
    """

    def invoke(self, state: AgentState, config: dict | None = None) -> AgentState:
        state = dict(state)

        while True:
            # ===== 1. 命题节点 =====
            state.update(generator_agent(state))

            # ===== 2. Fan-out: 并行验算 =====
            with ThreadPoolExecutor(max_workers=2) as executor:
                math_future = executor.submit(math_verifier, dict(state))
                phys_future = executor.submit(physics_verifier, dict(state))
                state.update(math_future.result())
                state.update(phys_future.result())

            # ===== 3. Fan-in: 仲裁 =====
            state.update(arbiter_agent(state))

            # ===== 4. 条件路由 =====
            route = _arbiter_router(state)

            if route == "pass":
                state.update(python_parser(state))
                state.update(formatting_agent(state))
                state.update(python_merger(state))
                return state

            elif route == "pass_with_edits":
                state["arbiter_decision"] = "PASS_WITH_EDITS"
                state.update(python_parser(state))
                state.update(formatting_agent(state))
                state.update(python_merger(state))
                return state

            elif route == "end":
                return state

            # route == "retry" → 循环继续


def build_graph() -> CompiledWorkflow:
    """构建并返回工作流对象。"""
    logger.info("[workflow] 工作流构建完成")
    return CompiledWorkflow()
