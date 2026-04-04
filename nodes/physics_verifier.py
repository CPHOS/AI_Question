"""
Node 2b: 物理验算 Agent（并行节点之一）。
仅写入 physics_review 字段，与 math_verifier 无写冲突。
"""
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_MAX_TOKENS, logger,
)
from config.prompts import PHYSICS_VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_PROMPT


def physics_verifier(state: AgentState) -> dict:
    """物理审核节点：验证题目的物理正确性、量纲一致性和模型自洽性。"""
    logger.info("[physics_verifier] 进入物理验算节点")

    llm = ChatOpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        model=BIG_MODEL_NAME,
        temperature=0.0,
        max_tokens=BIG_MODEL_MAX_TOKENS,
        max_retries=3,
    )

    response = llm.invoke([
        SystemMessage(content=PHYSICS_VERIFIER_SYSTEM_PROMPT),
        HumanMessage(content=VERIFIER_USER_PROMPT.format(
            draft_content=state["draft_content"],
        )),
    ])

    logger.info("[physics_verifier] 物理审核完成")
    return {"physics_review": response.content}
