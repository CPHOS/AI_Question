"""
解题生成 Agent。
接收 problem_text（题干 + 小问），输出 solution_text（参考答案 + 评分点 + 图像需求）。

数据归属（参见 model/state.py）：
  - 读取：GenerationOutput.problem_text + ArbitrationOutput.arbiter_feedback /
    solution_retry_count + TaskInput.total_score
  - 写入：GenerationOutput.solution_text 单字段
"""
import re
import time

from model.state import WorkflowData, GenerationOutput
from model.stats import record
from client import get_client, stream_chat
from config.config import (
    BIG_MODEL_NAME, BIG_MODEL_TEMPERATURE, BIG_MODEL_MAX_TOKENS, logger,
)
from prompts import load


def solution_generator_agent(data: WorkflowData) -> GenerationOutput:
    """解题生成节点：根据题干生成参考答案和评分点。

    retry 语义：`solution_retry_count` 由仲裁 Agent 在返回 `RETRY_SOLUTION` 时 +1。
    首轮生成读到 0，重试时读到 >=1，此节点不主动修改该计数器。

    返回值是 `GenerationOutput` 的子集（仅 solution_text），由状态机
    `data.update(...)` 合并到流转字典中。
    """
    retry = data.get("solution_retry_count", 0)
    logger.info("[solution_gen] 进入解题生成节点 | retry=%d", retry)

    client = get_client()

    system_prompt = load("solution_generator", "system_prompt",
                         total_score=str(data["total_score"]))

    if retry > 0 and data.get("arbiter_feedback"):
        user_prompt = load("solution_generator", "user_prompt_retry",
                           arbiter_feedback=data["arbiter_feedback"],
                           problem_text=data.get("problem_text", ""),
                           solution_text=data.get("solution_text", ""))
    else:
        user_prompt = load("solution_generator", "user_prompt_initial",
                           problem_text=data.get("problem_text", ""),
                           total_score=str(data["total_score"]))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("[solution_gen] 正在等待 thinking model...")
    t0 = time.time()
    content, usage = stream_chat(
        client, model=BIG_MODEL_NAME,
        messages=messages,
        temperature=BIG_MODEL_TEMPERATURE,
        max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info(
        "[solution_gen] 返回 | %d 字符 | %.0fs | tokens: %d+%d=%d",
        len(content), elapsed,
        usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
    )

    record(
        f"solution_gen_r{retry}", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    return {
        "solution_text": content,
    }
