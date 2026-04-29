"""
命题生成 Agent。
输出题干 + 小问结构（problem_text）。
不包含参考答案——解题由 solution_generator 独立完成。

数据归属（参见 model/state.py）：
  - 读取：TaskInput.* + ArbitrationOutput.arbiter_feedback / problem_retry_count
  - 写入：GenerationOutput 的 title / problem_text 字段；并主动清空上一轮
    ReviewOutput（math_review / physics_review / structure_review），避免
    旧审核串味。
"""
import re
import time

from model.state import WorkflowData, GenerationOutput, ReviewOutput
from model.stats import record
from client import get_client, stream_chat
from config.config import (
    BIG_MODEL_NAME, BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, logger,
)
from prompts import load


def _score_tier(total_score: int) -> str:
    """根据总分选择对应的分数段命题规模引导 key。"""
    if total_score < 40:
        return "score_guidance_low"
    elif total_score <= 60:
        return "score_guidance_mid"
    else:
        return "score_guidance_high"


def _strip_thinking_chain(content: str) -> str:
    """过滤 thinking model（如 Gemini）可能泄漏的思维链前缀。"""
    for marker in ["【题干】", "【题干】："]:
        idx = content.find(marker)
        if idx > 0:
            logger.info("[problem_gen] 检测到思维链前缀，从 '%s' 处截取", marker)
            content = content[idx:]
            break

    if re.search(r'\b(Wait|Let\'s check|Hmm|Actually|OK so)\b', content[:200]):
        logger.warning("[problem_gen] 输出疑似包含思维链碎片")
        last_idx = content.rfind("【题干】")
        if last_idx >= 0:
            content = content[last_idx:]

    return content


def problem_generator_agent(data: WorkflowData) -> dict:
    """命题生成节点：调用大模型生成题干与小问结构。

    retry 语义：`problem_retry_count` 由仲裁 Agent 在返回 `RETRY_PROBLEM` 时 +1。
    首轮生成读到 0，重试时读到 >=1，此节点不主动修改该计数器。

    返回字段同时覆盖两个阶段记录的子集（参见 model/state.py）：
      - `GenerationOutput`: title, problem_text
      - `ReviewOutput`: math_review / physics_review / structure_review 清零

    所以这里返回宽松的 `dict`（实质是 `GenerationOutput` ∪ `ReviewOutput` 子集），
    由状态机 `data.update(...)` 合并。
    """
    retry = data.get("problem_retry_count", 0)
    logger.info("[problem_gen] 进入命题生成节点 | retry=%d", retry)

    client = get_client()
    total_score = data["total_score"]

    score_guidance = load("problem_generator", _score_tier(total_score),
                          total_score=str(total_score))
    system_prompt = load("problem_generator", "system_prompt",
                         total_score=str(total_score))

    mode = data.get("mode", "topic_generation")
    planning_notes = data.get("planning_notes", "")

    if retry > 0 and data.get("arbiter_feedback"):
        user_prompt = load("problem_generator", "user_prompt_retry",
                           arbiter_feedback=data["arbiter_feedback"],
                           problem_text=data.get("problem_text", ""),
                           planning_notes=planning_notes)
    elif mode == "topic_generation":
        user_prompt = load("problem_generator", "user_prompt_topic",
                           topic=data["topic"],
                           difficulty=data["difficulty"],
                           total_score=str(total_score),
                           score_guidance=score_guidance,
                           planning_notes=planning_notes)
    else:
        # 改编类模式共用同一个 prompt 模板
        user_prompt = load("problem_generator", "user_prompt_adapt",
                           source_material=data.get("source_material", ""),
                           mode=mode,
                           difficulty=data["difficulty"],
                           total_score=str(total_score),
                           score_guidance=score_guidance,
                           planning_notes=planning_notes)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("[problem_gen] mode=%s | 正在等待 thinking model...", mode)
    t0 = time.time()
    content, usage = stream_chat(
        client, model=BIG_MODEL_NAME,
        messages=messages,
        temperature=BIG_MODEL_TEMPERATURE,
        max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info(
        "[problem_gen] 返回 | %d 字符 | %.0fs | tokens: %d+%d=%d",
        len(content), elapsed,
        usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
    )

    content = _strip_thinking_chain(content)

    # 提取标题
    title = ""
    title_match = re.match(r'【标题】\s*(.+?)\s*\n', content)
    if title_match:
        title = title_match.group(1).strip()
        content = content[title_match.end():]

    record(
        f"problem_gen_r{retry}", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    return {
        "title": title,
        "problem_text": content,
        # 清空上轮审核（本轮审核结果尚未产出）
        "math_review": "",
        "physics_review": "",
        "structure_review": "",
    }
