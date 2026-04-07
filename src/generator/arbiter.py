"""
仲裁 Agent。
使用 OpenAI Function Calling 确保输出可解析。
配合 try/except 兜底，即使结构化解析失败也不会崩溃。
每次执行 retry_count += 1。
"""
import json
import re
import time

from model.state import AgentState
from model.schema import ArbiterDecision
from model.stats import record
from client import get_client
from config.settings import (
    BIG_MODEL_NAME, ARBITER_MAX_TOKENS, logger,
)
from prompts import load


# 从 Pydantic 模型生成 OpenAI Function Calling 工具定义
_ARBITER_TOOLS = [{
    "type": "function",
    "function": {
        "name": "arbiter_decision",
        "description": "输出仲裁结构化裁决",
        "parameters": ArbiterDecision.model_json_schema(),
    },
}]


def _parse_text_response(text: str) -> tuple[str, str]:
    """
    从仲裁模型的纯文本响应中提取 decision 和 feedback。
    兼容 Gemini 等不支持 Function Calling 的模型。
    """
    text_upper = text.upper()

    # 尝试从 JSON 块中解析
    json_match = re.search(r'\{[^{}]*"decision"\s*:\s*"(PASS|RETRY|ABORT)"[^{}]*\}', text, re.IGNORECASE)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            return parsed.get("decision", "RETRY").strip().upper(), parsed.get("feedback", text)
        except json.JSONDecodeError:
            pass

    # 关键词匹配
    for keyword in ("PASS", "ABORT", "RETRY"):
        if keyword in text_upper:
            return keyword, text

    return "RETRY", f"[系统] 无法从文本中提取裁决，强制重试。原文: {text[:500]}"


def arbiter_agent(state: AgentState) -> dict:
    """仲裁节点：综合两份审核意见，输出结构化裁决。"""
    logger.info("[arbiter] 进入仲裁节点")

    client = get_client()

    messages = [
        {"role": "system", "content": load("arbiter", "system_prompt")},
        {"role": "user", "content": load("arbiter", "user_prompt",
            draft_content=state["draft_content"],
            math_review=state["math_review"],
            physics_review=state["physics_review"])},
    ]

    elapsed = 0.0
    p_tok = 0
    c_tok = 0
    t_tok = 0
    try:
        logger.info("[arbiter] 正在等待 thinking model 仲裁...")
        t0 = time.time()
        resp = client.create(
            model=BIG_MODEL_NAME,
            messages=messages,
            temperature=0.0,
            max_tokens=ARBITER_MAX_TOKENS,
            tools=_ARBITER_TOOLS,
            tool_choice={"type": "function", "function": {"name": "arbiter_decision"}},
        )
        elapsed = time.time() - t0
        usage = resp.usage
        p_tok = usage.prompt_tokens if usage else 0
        c_tok = usage.completion_tokens if usage else 0
        t_tok = usage.total_tokens if usage else 0
        logger.info(f"[arbiter] 仲裁响应到达 | {elapsed:.0f}s | tokens: {p_tok}+{c_tok}={t_tok}")

        msg = resp.choices[0].message

        # 优先从 tool_calls 解析（标准路径）
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            result = json.loads(tool_call.function.arguments)
            parsed = ArbiterDecision(**result)
            decision = parsed.decision.strip().upper()
            reason = parsed.reason
            feedback = parsed.feedback
            error_category = parsed.error_category.strip().lower()
            if error_category not in ("none", "style", "fatal"):
                error_category = "fatal"
        else:
            # Fallback: 模型未使用 tool_calls（如 Gemini via OpenRouter），从文本内容解析
            logger.warning("[arbiter] 模型未返回 tool_calls，尝试从文本内容解析")
            raw = msg.content or ""
            decision, feedback = _parse_text_response(raw)
            reason = feedback[:200]
            error_category = "fatal"

        # 校验 decision 是否为合法值
        if decision not in ("PASS", "RETRY", "ABORT"):
            logger.warning(f"[arbiter] 非法 decision: '{decision}'，强制视为 RETRY")
            decision = "RETRY"
            feedback = f"[系统] 仲裁返回非法值'{decision}'，强制重试。原始反馈: {feedback}"

    except Exception as e:
        logger.error(f"[arbiter] 结构化解析失败: {e}，触发兜底 RETRY")
        decision = "RETRY"
        reason = f"系统错误: {str(e)}"
        feedback = f"[系统错误] 仲裁解析失败，强制重试。异常: {str(e)}"
        error_category = "fatal"

    new_retry = state.get("retry_count", 0) + 1
    logger.info(f"[arbiter] 裁决={decision} | 理由={reason[:80]} | retry_count 递增至 {new_retry}")

    record(
        f"arbiter_r{new_retry}", 0, elapsed, extra=decision,
        prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=t_tok,
    )

    return {
        "arbiter_decision": decision,
        "arbiter_reason": reason,
        "arbiter_feedback": feedback,
        "error_category": error_category,
        "retry_count": new_retry,
    }
