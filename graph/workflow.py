"""
LangGraph 状态机编排。

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
from langgraph.graph import StateGraph, END
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
    返回值必须是 add_conditional_edges 映射字典的 key。
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


def build_graph():
    """
    构建并编译 LangGraph 工作流。
    返回编译后的 CompiledStateGraph，可直接 .invoke() 调用。
    """
    graph = StateGraph(AgentState)

    # ===== 注册所有节点 =====
    graph.add_node("generator_agent", generator_agent)
    graph.add_node("math_verifier", math_verifier)
    graph.add_node("physics_verifier", physics_verifier)
    graph.add_node("arbiter_agent", arbiter_agent)
    graph.add_node("python_parser", python_parser)
    graph.add_node("formatting_agent", formatting_agent)
    graph.add_node("python_merger", python_merger)

    # ===== 入口 =====
    graph.set_entry_point("generator_agent")

    # ===== Fan-out: generator → [math_verifier, physics_verifier] 并行 =====
    graph.add_edge("generator_agent", "math_verifier")
    graph.add_edge("generator_agent", "physics_verifier")

    # ===== Fan-in: [math_verifier, physics_verifier] → arbiter =====
    graph.add_edge("math_verifier", "arbiter_agent")
    graph.add_edge("physics_verifier", "arbiter_agent")

    # ===== 条件路由: arbiter → {pass, retry, end} =====
    graph.add_conditional_edges(
        "arbiter_agent",
        _arbiter_router,
        {
            "pass": "python_parser",
            "retry": "generator_agent",
            "end": END,
        },
    )

    # ===== 后处理流水线 =====
    graph.add_edge("python_parser", "formatting_agent")
    graph.add_edge("formatting_agent", "python_merger")
    graph.add_edge("python_merger", END)

    # ===== 编译 =====
    compiled = graph.compile()
    logger.info("[workflow] 状态图编译完成")
    return compiled
