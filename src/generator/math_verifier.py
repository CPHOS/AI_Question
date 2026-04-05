"""
数学验算 Agent（并行节点之一）。
仅写入 math_review 字段，与 physics_verifier 无写冲突。
"""
import time

from model.state import AgentState
from model.stats import record
from client import get_client, stream_chat
from config.settings import BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, logger
from prompts import load


def math_verifier(state: AgentState) -> dict:
    """数学审核节点：验证解答中所有数学推导的正确性。"""
    logger.info("[math_verifier] 进入数学验算节点")

    client = get_client()
    messages = [
        {"role": "system", "content": load("verifier", "math_verifier_system_prompt")},
        {"role": "user", "content": load("verifier", "user_prompt",
            draft_content=state["draft_content"])},
    ]

    logger.info("[math_verifier] 正在等待 thinking model 审核...")
    t0 = time.time()
    content, usage = stream_chat(
        client,
        model=BIG_MODEL_NAME,
        messages=messages,
        temperature=0.0,
        max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info(
        f"[math_verifier] 数学审核完成 | {len(content)} 字符 | {elapsed:.0f}s | "
        f"tokens: {usage.prompt_tokens}+{usage.completion_tokens}={usage.total_tokens}"
    )

    record(
        "math_verifier", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    return {"math_review": content}
