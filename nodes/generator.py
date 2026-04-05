"""
Node 1: 命题 Agent。
- retry_count == 0: 根据 topic + difficulty 全新生成。
- retry_count > 0:  根据 arbiter_feedback 针对性修改，同时清空上轮 review。
"""
import re
import time
from openai import OpenAI
from state.schema import AgentState
from config.settings import (
    BIG_MODEL_API_KEY, BIG_MODEL_BASE_URL, BIG_MODEL_NAME,
    BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, BIG_MODEL_TIMEOUT, logger,
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

    client = OpenAI(
        api_key=BIG_MODEL_API_KEY,
        base_url=BIG_MODEL_BASE_URL,
        timeout=BIG_MODEL_TIMEOUT,
        max_retries=3,
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

    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("[generator] 正在等待 thinking model 思考（可能需要 1-5 分钟）...")
    t0 = time.time()
    response = client.chat.completions.create(
        model=BIG_MODEL_NAME,
        messages=messages,
        temperature=BIG_MODEL_TEMPERATURE,
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
    logger.info(f"[generator] 命题模型返回 | {len(content)} 字符 | {elapsed:.0f}s | tokens: {p_tok}+{c_tok}={t_tok}")

    # ===== 思维链过滤（兼容 thinking model 如 Gemini 3.1 Pro） =====
    # 如果输出包含【题干】标记，截取从该标记开始的正式内容
    for marker in ["【题干】", "【题干】："]:
        idx = content.find(marker)
        if idx > 0:
            logger.info(f"[generator] 检测到思维链前缀，从 '{marker}' 处截取正式内容")
            content = content[idx:]
            break

    # 如果仍然包含明显的思维链碎片（英文自言自语），尝试清理
    if re.search(r'\b(Wait|Let\'s check|Hmm|Actually|OK so)\b', content[:200]):
        logger.warning("[generator] 输出疑似包含思维链碎片，尝试提取结构化部分")
        # 尝试找最后一个【题干】
        last_idx = content.rfind("【题干】")
        if last_idx >= 0:
            content = content[last_idx:]

    logger.info(f"[generator] 命题完成 | 输出长度={len(content)} 字符")

    from utils.run_stats import record
    record(f"generator_r{retry}", len(content), elapsed,
           prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=t_tok)

    # 清空上一轮的 review 状态，防止脏数据流入下一轮仲裁
    return {
        "draft_content": content,
        "math_review": "",
        "physics_review": "",
    }
