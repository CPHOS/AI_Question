"""
命题规划器。
根据 TaskSpec（模式 + 难度目标 + 源材料）生成 planning_notes。
planning_notes 作为后续命题和解题 Agent 的上下文输入。

数据归属（参见 model/state.py）：
  - 读取：TaskInput.{mode, topic, difficulty, total_score, difficulty_profile,
    source_material}
  - 写入：TaskInput.planning_notes
"""
import time

from model.state import WorkflowData, TaskInput
from model.stats import record
from client import get_client, stream_chat
from config.config import BIG_MODEL_NAME, BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, logger
from prompts import load


def _build_difficulty_text(profile: dict) -> str:
    """将难度目标字典转为 prompt 可读文本。"""
    comp = profile.get("target_computation", 6)
    think = profile.get("target_thinking", 6)
    overall = profile.get("target_overall", 7)
    q_count = profile.get("question_count", 4)
    dist = profile.get("score_distribution", [])
    dist_text = f"，建议分值分配: {dist}" if dist else ""
    return (
        f"计算难度目标: {comp}/10，思维难度目标: {think}/10，"
        f"综合难度目标: {overall}/10，预期小问数量: {q_count}{dist_text}"
    )


def run_planning(data: WorkflowData) -> TaskInput:
    """规划节点：根据模式和输入生成 planning_notes。

    返回 `TaskInput` 子集（仅 planning_notes），由状态机合并到流转字典。
    """
    mode = data.get("mode", "topic_generation")
    logger.info("[planner] 进入命题规划节点 | mode=%s", mode)

    client = get_client()
    difficulty_text = _build_difficulty_text(data.get("difficulty_profile", {}))

    system_prompt = load("planning", "system_prompt")
    user_prompt = load(
        "planning", f"user_prompt_{mode}",
        topic=data.get("topic", ""),
        difficulty=data.get("difficulty", ""),
        total_score=str(data.get("total_score", 40)),
        difficulty_text=difficulty_text,
        source_material=data.get("source_material", ""),
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

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
        "[planner] 规划完成 | %d 字符 | %.0fs | tokens: %d+%d=%d",
        len(content), elapsed,
        usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
    )

    record(
        "planner", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    return {"planning_notes": content}
