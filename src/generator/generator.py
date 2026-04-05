"""
命题 Agent。
- retry_count == 0: 根据 topic + difficulty 全新生成。
- retry_count > 0:  根据 arbiter_feedback 针对性修改，同时清空上轮 review。
"""
import re
import time

from model.state import AgentState
from model.stats import record
from client import get_client, stream_chat
from config.settings import (
    BIG_MODEL_NAME, BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, logger,
)
from prompts import load


def generator_agent(state: AgentState) -> dict:
    """命题节点：调用大模型生成或修改物理竞赛题。"""
    retry = state.get("retry_count", 0)
    logger.info(f"[generator] 进入命题节点 | retry_count={retry}")

    client = get_client()

    if retry == 0:
        user_prompt = load("generator", "user_prompt_initial",
                           topic=state["topic"], difficulty=state["difficulty"])
    else:
        user_prompt = load("generator", "user_prompt_retry",
                           arbiter_feedback=state["arbiter_feedback"],
                           draft_content=state["draft_content"])

    messages = [
        {"role": "system", "content": load("generator", "system_prompt")},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("[generator] 正在等待 thinking model 思考（可能需要 1-5 分钟）...")
    t0 = time.time()
    content, usage = stream_chat(
        client,
        model=BIG_MODEL_NAME,
        messages=messages,
        temperature=BIG_MODEL_TEMPERATURE,
        max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info(
        f"[generator] 命题模型返回 | {len(content)} 字符 | {elapsed:.0f}s | "
        f"tokens: {usage.prompt_tokens}+{usage.completion_tokens}={usage.total_tokens}"
    )

    # ===== 思维链过滤（兼容 thinking model 如 Gemini 3.1 Pro） =====
    for marker in ["【题干】", "【题干】："]:
        idx = content.find(marker)
        if idx > 0:
            logger.info(f"[generator] 检测到思维链前缀，从 '{marker}' 处截取正式内容")
            content = content[idx:]
            break

    if re.search(r'\b(Wait|Let\'s check|Hmm|Actually|OK so)\b', content[:200]):
        logger.warning("[generator] 输出疑似包含思维链碎片，尝试提取结构化部分")
        last_idx = content.rfind("【题干】")
        if last_idx >= 0:
            content = content[last_idx:]

    logger.info(f"[generator] 命题完成 | 输出长度={len(content)} 字符")

    record(
        f"generator_r{retry}", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    return {
        "draft_content": content,
        "math_review": "",
        "physics_review": "",
    }
