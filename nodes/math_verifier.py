"""
Node 2a: 数学验算 Agent（并行节点之一）。
仅写入 math_review 字段，与 physics_verifier 无写冲突。
"""
import time
from openai import OpenAI
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_MAX_TOKENS, BIG_MODEL_TIMEOUT, logger,
)
from config.prompts import MATH_VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_PROMPT


def math_verifier(state: AgentState) -> dict:
    """数学审核节点：验证解答中所有数学推导的正确性。"""
    logger.info("[math_verifier] 进入数学验算节点")

    client = OpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        timeout=BIG_MODEL_TIMEOUT,
        max_retries=3,
    )

    messages = [
        {"role": "system", "content": MATH_VERIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": VERIFIER_USER_PROMPT.format(
            draft_content=state["draft_content"],
        )},
    ]

    logger.info("[math_verifier] 正在等待 thinking model 审核...")
    t0 = time.time()
    response = client.chat.completions.create(
        model=BIG_MODEL_NAME,
        messages=messages,
        temperature=0.0,
        max_tokens=BIG_MODEL_MAX_TOKENS,
        stream=True,
        stream_options={"include_usage": True},
    )
    content = ""
    usage = None
    for chunk in response:
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            content += chunk.choices[0].delta.content
    elapsed = time.time() - t0
    p_tok = usage.prompt_tokens if usage else 0
    c_tok = usage.completion_tokens if usage else 0
    t_tok = usage.total_tokens if usage else 0
    logger.info(f"[math_verifier] 数学审核完成 | {len(content)} 字符 | {elapsed:.0f}s | tokens: {p_tok}+{c_tok}={t_tok}")

    from utils.run_stats import record
    record("math_verifier", len(content), elapsed,
           prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=t_tok)

    return {"math_review": content}
