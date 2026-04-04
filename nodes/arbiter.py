"""
Node 3: 仲裁 Agent。
使用 Pydantic with_structured_output 确保输出可解析。
配合 try/except 兜底，即使结构化解析失败也不会崩溃。
每次执行 retry_count += 1。
"""
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_MAX_TOKENS, logger,
)
from config.prompts import ARBITER_SYSTEM_PROMPT, ARBITER_USER_PROMPT


class ArbiterDecision(BaseModel):
    """仲裁结构化输出模型。with_structured_output 会强制 LLM 输出此格式。"""
    decision: str = Field(
        description="必须严格输出 'PASS', 'RETRY', 或 'ABORT' 三者之一"
    )
    feedback: str = Field(
        description="综合评审意见及修改指导；若 PASS 则写'无需修改'"
    )


def arbiter_agent(state: AgentState) -> dict:
    """仲裁节点：综合两份审核意见，输出结构化裁决。"""
    logger.info("[arbiter] 进入仲裁节点")

    llm = ChatOpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        model=BIG_MODEL_NAME,
        temperature=0.0,
        max_tokens=BIG_MODEL_MAX_TOKENS,
        max_retries=3,
    )

    # 使用 Pydantic 结构化输出（底层走 OpenAI Function Calling）
    structured_llm = llm.with_structured_output(ArbiterDecision)

    messages = [
        SystemMessage(content=ARBITER_SYSTEM_PROMPT),
        HumanMessage(content=ARBITER_USER_PROMPT.format(
            draft_content=state["draft_content"],
            math_review=state["math_review"],
            physics_review=state["physics_review"],
        )),
    ]

    try:
        response: ArbiterDecision = structured_llm.invoke(messages)
        decision = response.decision.strip().upper()
        feedback = response.feedback

        # 校验 decision 是否为合法值
        if decision not in ("PASS", "RETRY", "ABORT"):
            logger.warning(f"[arbiter] 非法 decision: '{decision}'，强制视为 RETRY")
            decision = "RETRY"
            feedback = f"[系统] 仲裁返回非法值'{response.decision}'，强制重试。原始反馈: {feedback}"

    except Exception as e:
        logger.error(f"[arbiter] 结构化解析失败: {e}，触发兜底 RETRY")
        decision = "RETRY"
        feedback = f"[系统错误] 仲裁解析失败，强制重试。异常: {str(e)}"

    new_retry = state.get("retry_count", 0) + 1
    logger.info(f"[arbiter] 裁决={decision} | retry_count 递增至 {new_retry}")

    return {
        "arbiter_decision": decision,
        "arbiter_feedback": feedback,
        "retry_count": new_retry,
    }
