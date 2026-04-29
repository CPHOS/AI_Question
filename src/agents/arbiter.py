"""
仲裁 Agent。
使用 OpenAI Function Calling 确保输出可解析。
裁决类型: PASS / RETRY_PROBLEM / RETRY_SOLUTION / ABORT。

重试计数语义（分阶段计数）：
  - `RETRY_PROBLEM` → problem_retry_count += 1
  - `RETRY_SOLUTION` → solution_retry_count += 1
  - `PASS` / `ABORT` → 不递增（首轮直接通过不计 retry）
  - `retry_count` 是两者之和，只作为总重试次数的元数据展示。

数据归属（参见 model/state.py）：
  - 读取：GenerationOutput.draft_content + ReviewOutput.* + 自身上一轮的
    problem_retry_count / solution_retry_count
  - 写入：ArbitrationOutput 全部字段（decision / reason / feedback /
    error_category / *_retry_count）
"""
import json
import re
import time

from pydantic import ValidationError

from model.state import WorkflowData, ArbitrationOutput
from model.schema import ArbiterDecision
from model.stats import record, get_all as _get_stats
from client import get_client
from config.config import (
    BIG_MODEL_NAME, ARBITER_MAX_TOKENS, logger,
)
from prompts import load


_VALID_DECISIONS = ("PASS", "RETRY_PROBLEM", "RETRY_SOLUTION", "ABORT")
_VALID_ERROR_CATEGORIES = ("none", "style", "fatal")

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
    json_match = re.search(
        r'\{[^{}]*"decision"\s*:\s*"(PASS|RETRY_PROBLEM|RETRY_SOLUTION|ABORT)"[^{}]*\}',
        text, re.IGNORECASE,
    )
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            return parsed.get("decision", "RETRY_PROBLEM").strip().upper(), parsed.get("feedback", text)
        except json.JSONDecodeError:
            pass

    # 关键词匹配（按优先级排序，先匹配更具体的）
    for keyword in ("RETRY_PROBLEM", "RETRY_SOLUTION", "PASS", "ABORT"):
        if keyword in text_upper:
            return keyword, text

    return "RETRY_PROBLEM", f"[系统] 无法从文本中提取裁决，强制重试命题。原文: {text[:500]}"


def arbiter_agent(data: WorkflowData) -> ArbitrationOutput:
    """仲裁节点：综合三份审核意见，输出结构化裁决。

    返回完整的 `ArbitrationOutput`（decision / reason / feedback /
    error_category 以及更新后的三个 retry 计数器），由状态机合并。
    """
    logger.info("[arbiter] 进入仲裁节点")

    client = get_client()

    messages = [
        {"role": "system", "content": load("arbiter", "system_prompt")},
        {"role": "user", "content": load("arbiter", "user_prompt",
            draft_content=data["draft_content"],
            math_review=data["math_review"],
            physics_review=data["physics_review"],
            structure_review=data.get("structure_review", ""))},
    ]

    elapsed = 0.0
    p_tok = c_tok = t_tok = 0

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
        logger.info("[arbiter] 响应到达 | %.0fs | tokens: %d+%d=%d", elapsed, p_tok, c_tok, t_tok)

        msg = resp.choices[0].message

        # 优先从 tool_calls 解析
        if msg.tool_calls:
            raw_args = msg.tool_calls[0].function.arguments
            result = json.loads(raw_args)

            # 兼容旧版 "RETRY" → "RETRY_PROBLEM"。Pydantic Literal 校验会拒绝 "RETRY"，
            # 所以必须在解析前做这步映射；命中时打 INFO 日志，便于观察该兼容分支
            # 是否仍被新模型触发（若长期为零可考虑彻底移除）。
            if result.get("decision", "").strip().upper() == "RETRY":
                logger.info("[arbiter] 收到旧值 decision='RETRY'，自动映射为 RETRY_PROBLEM")
                result["decision"] = "RETRY_PROBLEM"

            try:
                parsed = ArbiterDecision(**result)
            except ValidationError as ve:
                # Pydantic Literal 校验失败：明确告知哪个字段非法，再走结构化降级。
                bad_decision = result.get("decision")
                bad_category = result.get("error_category")
                logger.warning(
                    "[arbiter] tool_calls 字段非法 (decision=%r, error_category=%r) → 走文本兜底 | %s",
                    bad_decision, bad_category, ve.errors(include_url=False)[:3],
                )
                raw = msg.content or json.dumps(result, ensure_ascii=False)
                decision, feedback = _parse_text_response(raw)
                reason = feedback[:200]
                # 文本兜底无法可靠判定 error_category：fatal 是保守选择，但必须显式记录。
                error_category = "fatal"
                logger.warning(
                    "[arbiter] 文本兜底无法判定 error_category，按保守策略置为 'fatal'"
                )
            else:
                decision = parsed.decision  # 已被 Literal 收敛
                reason = parsed.reason
                feedback = parsed.feedback
                error_category = parsed.error_category  # 已被 Literal 收敛
        else:
            # Fallback: 从文本内容解析
            logger.warning("[arbiter] 模型未返回 tool_calls，尝试从文本解析")
            raw = msg.content or ""
            decision, feedback = _parse_text_response(raw)
            reason = feedback[:200]
            # 文本兜底没有结构化 error_category 信号：fatal 是保守默认，必须显式记录。
            error_category = "fatal"
            logger.warning(
                "[arbiter] 文本兜底无法判定 error_category，按保守策略置为 'fatal'"
            )

        # 二次防御：经过 Pydantic 后值理论上必合法，仍校验一次以防 _parse_text_response 输出
        if decision not in _VALID_DECISIONS:
            raw_decision = decision
            logger.warning("[arbiter] 非法 decision: '%s'，强制视为 RETRY_PROBLEM", raw_decision)
            decision = "RETRY_PROBLEM"
            feedback = f"[系统] 仲裁返回非法值'{raw_decision}'，强制重试。原始反馈: {feedback}"
        if error_category not in _VALID_ERROR_CATEGORIES:
            logger.warning(
                "[arbiter] 非法 error_category: '%s'，强制视为 'fatal'", error_category
            )
            error_category = "fatal"

    except Exception as e:
        logger.error("[arbiter] 结构化解析失败: %s，触发兜底 RETRY_PROBLEM", e)
        decision = "RETRY_PROBLEM"
        reason = f"系统错误: {str(e)}"
        feedback = f"[系统错误] 仲裁解析失败，强制重试。异常: {str(e)}"
        error_category = "fatal"

    # ===== 分阶段重试计数 =====
    # 只有实际触发 RETRY_* 才递增对应阶段计数；PASS / ABORT 不计。
    prob_retry = data.get("problem_retry_count", 0)
    sol_retry = data.get("solution_retry_count", 0)

    if decision == "RETRY_PROBLEM":
        prob_retry += 1
    elif decision == "RETRY_SOLUTION":
        sol_retry += 1

    total_retry = prob_retry + sol_retry
    logger.info(
        "[arbiter] 裁决=%s | 理由=%s | problem_retry=%d solution_retry=%d (total=%d)",
        decision, reason[:80], prob_retry, sol_retry, total_retry,
    )

    # 统计 key 使用独立的"本轮仲裁次数"（按现有 arbiter_r* 数 +1），
    # 避免 PASS/ABORT 不递增 total_retry 时覆盖上一轮的统计记录。
    arb_seq = sum(1 for k in _get_stats() if k.startswith("arbiter_r")) + 1
    record(
        f"arbiter_r{arb_seq}", 0, elapsed, extra=decision,
        prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=t_tok,
    )

    return {
        "arbiter_decision": decision,
        "arbiter_reason": reason,
        "arbiter_feedback": feedback,
        "error_category": error_category,
        "problem_retry_count": prob_retry,
        "solution_retry_count": sol_retry,
        "retry_count": total_retry,
    }
