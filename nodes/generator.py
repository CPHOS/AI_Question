"""
Node 1: 命题 Agent。
- retry_count == 0: 根据 topic + difficulty 全新生成。
- retry_count > 0:  根据 arbiter_feedback 针对性修改，同时清空上轮 review。
"""
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, logger,
)
from config.prompts import (
    GENERATOR_SYSTEM_PROMPT,
    GENERATOR_USER_PROMPT_INITIAL,
    GENERATOR_USER_PROMPT_RETRY,
)


def generator_agent(state: AgentState) -> dict:
    """命题节点：调用大模型生成或修改物理竞赛题。"""
    retry = state.get("retry_count", 0)
    logger.info(f"[generator] 进入命题节点 | retry_count={retry}")

    llm = ChatOpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        model=BIG_MODEL_NAME,
        temperature=BIG_MODEL_TEMPERATURE,
        max_tokens=BIG_MODEL_MAX_TOKENS,
        max_retries=3,  # API 级别自动重试
    )

    if retry == 0:
        user_prompt = GENERATOR_USER_PROMPT_INITIAL.format(
            topic=state["topic"],
            difficulty=state["difficulty"],
        )
    else:
        user_prompt = GENERATOR_USER_PROMPT_RETRY.format(
            arbiter_feedback=state["arbiter_feedback"],
            draft_content=state["draft_content"],
        )

    response = llm.invoke([
        SystemMessage(content=GENERATOR_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    logger.info(f"[generator] 命题完成 | 输出长度={len(response.content)} 字符")

    # 清空上一轮的 review 状态，防止脏数据流入下一轮仲裁
    return {
        "draft_content": response.content,
        "math_review": "",
        "physics_review": "",
    }
