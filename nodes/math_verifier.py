"""
Node 2a: 数学验算 Agent（并行节点之一）。
仅写入 math_review 字段，与 physics_verifier 无写冲突。
"""
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_MAX_TOKENS, logger,
)
from config.prompts import MATH_VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_PROMPT


def math_verifier(state: AgentState) -> dict:
    """数学审核节点：验证解答中所有数学推导的正确性。"""
    logger.info("[math_verifier] 进入数学验算节点")

    llm = ChatOpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        model=BIG_MODEL_NAME,
        temperature=0.0,  # 审核不需要创造性
        max_tokens=BIG_MODEL_MAX_TOKENS,
        max_retries=3,
    )

    response = llm.invoke([
        SystemMessage(content=MATH_VERIFIER_SYSTEM_PROMPT),
        HumanMessage(content=VERIFIER_USER_PROMPT.format(
            draft_content=state["draft_content"],
        )),
    ])

    logger.info("[math_verifier] 数学审核完成")
    return {"math_review": response.content}
