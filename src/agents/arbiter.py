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


# 从 Pydantic 模型生成 OpenAI Function Calling 工具定义
_ARBITER_TOOLS = [{
    "type": "function",
    "function": {
        "name": "arbiter_decision",
        "description": "输出仲裁结构化裁决",
        "parameters": ArbiterDecision.model_json_schema(),
    },
}]


def _call_arbiter_model(client, messages: list[dict[str, str]], *, label: str):
    """调用仲裁模型并返回响应、耗时和 token 统计。"""
    logger.info("[arbiter] 正在等待 thinking model 仲裁 (%s)...", label)
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
    logger.info(
        "[arbiter] 响应到达 (%s) | %.0fs | tokens: %d+%d=%d",
        label, elapsed, p_tok, c_tok, t_tok,
    )
    return resp, elapsed, p_tok, c_tok, t_tok


def _parse_tool_decision(resp) -> tuple[ArbiterDecision | None, object, Exception | None]:
    """从 tool_calls 中解析仲裁结果；解析失败时返回原始载荷与异常。"""
    msg = resp.choices[0].message
    if not msg.tool_calls:
        return None, None, ValueError("模型未返回 arbiter_decision 工具调用")

    raw_args = msg.tool_calls[0].function.arguments
    try:
        payload = json.loads(raw_args)
        return ArbiterDecision(**payload), payload, None
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        return None, raw_args, exc


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return json.dumps(exc.errors(include_url=False), ensure_ascii=False, default=str)
    return str(exc)


def _build_relabel_messages(
    messages: list[dict[str, str]],
    bad_payload: object,
    exc: Exception,
) -> list[dict[str, str]]:
    payload_text = (
        json.dumps(bad_payload, ensure_ascii=False)
        if isinstance(bad_payload, dict)
        else str(bad_payload)
    )
    return messages + [{
        "role": "user",
        "content": (
            "你上一次返回的 arbiter_decision 工具调用字段非法，"
            "请只重新调用 arbiter_decision 工具，不要重新审题，不要输出正文。\n"
            f"非法载荷：{payload_text}\n"
            f"校验错误：{_error_summary(exc)}\n"
            "合法 decision 只能是 PASS / RETRY_PROBLEM / RETRY_SOLUTION / ABORT。\n"
            "合法 error_category 只能是 none / style / fatal。\n"
            "合法组合为：PASS+none/style，RETRY_PROBLEM+fatal，"
            "RETRY_SOLUTION+fatal，ABORT+fatal。"
        ),
    }]


def arbiter_agent(data: WorkflowData) -> ArbitrationOutput:
    """仲裁节点：综合四路审核意见，输出结构化裁决。

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
            structure_review=data.get("structure_review", ""),
            quality_review=data.get("quality_review", ""))},
    ]

    elapsed = 0.0
    p_tok = c_tok = t_tok = 0

    try:
        resp, elapsed, p_tok, c_tok, t_tok = _call_arbiter_model(
            client, messages, label="initial"
        )
        parsed, bad_payload, parse_error = _parse_tool_decision(resp)

        if parse_error is not None:
            logger.warning(
                "[arbiter] tool_calls 字段非法，要求仲裁重新打标签 | %s",
                _error_summary(parse_error),
            )
            repair_messages = _build_relabel_messages(messages, bad_payload, parse_error)
            repair_resp, repair_elapsed, repair_p, repair_c, repair_t = _call_arbiter_model(
                client, repair_messages, label="relabel"
            )
            elapsed += repair_elapsed
            p_tok += repair_p
            c_tok += repair_c
            t_tok += repair_t
            parsed, bad_payload, parse_error = _parse_tool_decision(repair_resp)

        if parse_error is not None or parsed is None:
            logger.error(
                "[arbiter] 重新打标签仍失败，终止本题 | payload=%r | error=%s",
                bad_payload, _error_summary(parse_error or ValueError("empty result")),
            )
            decision = "ABORT"
            reason = "仲裁模型返回的结构化标签非法，重新打标签后仍无法通过校验。"
            feedback = (
                "[系统] 仲裁结构化标签非法，流程终止。请检查仲裁提示词或模型工具调用支持。"
            )
            error_category = "fatal"
        else:
            decision = parsed.decision
            reason = parsed.reason
            feedback = parsed.feedback
            error_category = parsed.error_category

    except Exception as e:
        logger.error("[arbiter] 仲裁执行失败: %s，终止本题", e)
        decision = "ABORT"
        reason = f"系统错误: {str(e)}"
        feedback = f"[系统错误] 仲裁执行失败，流程终止。异常: {str(e)}"
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
