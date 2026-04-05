"""
纯 Python 状态机编排（替代 LangGraph）。

流程图:
  START → generator_agent
            │
            ├─────────────────┐
            ▼                 ▼
      math_verifier    physics_verifier    (并行 Fan-out，写不同 key，无需 reducer)
            │                 │
            └────────┬────────┘
                     ▼
              arbiter_agent                 (Fan-in)
                     │
          ┌──────────┼───────────┐
          ▼          ▼           ▼
        PASS       RETRY     ABORT/max
          │          │           │
          ▼          ▼           ▼
     python_parser  generator   END
          │
          ▼
     formatting_agent
          │
          ▼
     python_merger
          │
          ▼
         END
"""
from concurrent.futures import ThreadPoolExecutor
from state.schema import AgentState
from nodes.generator import generator_agent
from nodes.math_verifier import math_verifier
from nodes.physics_verifier import physics_verifier
from nodes.arbiter import arbiter_agent
from nodes.parser import python_parser
from nodes.formatter import formatting_agent
from nodes.merger import python_merger
from config.settings import MAX_RETRY_COUNT, logger


def _arbiter_router(state: AgentState) -> str:
    """
    仲裁后的条件路由。
    返回值: "pass" | "retry" | "end"
    优先级: PASS > ABORT > 重试上限 > RETRY
    """
    decision = state.get("arbiter_decision", "RETRY")
    retry = state.get("retry_count", 0)

    # PASS 优先级最高：即使到了最大重试次数，只要通过就走后处理
    if decision == "PASS":
        logger.info("[router] PASS → 进入后处理流水线")
        return "pass"

    if decision == "ABORT":
        logger.warning("[router] 仲裁判定 ABORT → 流程终止")
        return "end"

    if retry >= MAX_RETRY_COUNT:
        logger.warning(f"[router] 达到最大重试 {MAX_RETRY_COUNT} 次 → 强制终止")
        return "end"

    # decision == "RETRY"
    logger.info(f"[router] RETRY → 回到命题节点 (已重试 {retry} 次)")
    return "retry"


class CompiledWorkflow:
    """
    编译后的工作流对象，提供与原 LangGraph CompiledStateGraph
    兼容的 .invoke(state, config=...) 接口。
    """

    def invoke(self, state: AgentState, config: dict | None = None) -> AgentState:
        """
        执行完整工作流。

        config 参数保留以兼容原有调用方式（如 recursion_limit），
        但纯 Python 实现中由 MAX_RETRY_COUNT 控制循环上限。
        """
        state = dict(state)  # 浅拷贝，避免修改原始输入

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
                # ===== 后处理流水线 =====
                state.update(python_parser(state))
                state.update(formatting_agent(state))
                state.update(python_merger(state))
                return state

            elif route == "end":
                return state

            # route == "retry" → 循环继续


def build_graph() -> CompiledWorkflow:
    """
    构建并返回工作流对象。
    返回 CompiledWorkflow 实例，可直接 .invoke() 调用。
    """
    logger.info("[workflow] 工作流构建完成")
    return CompiledWorkflow()
