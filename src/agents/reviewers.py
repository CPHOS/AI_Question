"""
审核 Agent 统一入口。
包含数学检查、物理检查、结构检查三个子 Agent，并行执行。

数据归属（参见 model/state.py）：
  - 读取：GenerationOutput.draft_content / problem_text / solution_text
    + TaskInput.total_score
  - 写入：ReviewOutput 的三个字段（math_review / physics_review / structure_review）
    分别由三个互斥子 Agent 写入（无写冲突）。
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from model.state import WorkflowData, ReviewOutput
from model.stats import record
from client import get_client, stream_chat
from config.config import BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, logger
from prompts import load


# ------------------------------------------------------------------
# 数学检查
# ------------------------------------------------------------------

def _math_check(data: WorkflowData) -> ReviewOutput:
    """数学检查：验证解答中所有数学推导的正确性。"""
    logger.info("[math_check] 进入数学检查节点")
    client = get_client()

    messages = [
        {"role": "system", "content": load("reviewers", "math_system_prompt")},
        {"role": "user", "content": load("reviewers", "review_user_prompt",
            draft_content=data["draft_content"])},
    ]

    t0 = time.time()
    content, usage = stream_chat(
        client, model=BIG_MODEL_NAME,
        messages=messages, temperature=0.0, max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info("[math_check] 完成 | %d 字符 | %.0fs", len(content), elapsed)

    record(
        "math_check", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
    return {"math_review": content}


# ------------------------------------------------------------------
# 物理检查
# ------------------------------------------------------------------

def _physics_check(data: WorkflowData) -> ReviewOutput:
    """物理检查：验证题目的物理正确性、量纲一致性和模型自洽性。"""
    logger.info("[physics_check] 进入物理检查节点")
    client = get_client()

    messages = [
        {"role": "system", "content": load("reviewers", "physics_system_prompt")},
        {"role": "user", "content": load("reviewers", "review_user_prompt",
            draft_content=data["draft_content"])},
    ]

    t0 = time.time()
    content, usage = stream_chat(
        client, model=BIG_MODEL_NAME,
        messages=messages, temperature=0.0, max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info("[physics_check] 完成 | %d 字符 | %.0fs", len(content), elapsed)

    record(
        "physics_check", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
    return {"physics_review": content}


# ------------------------------------------------------------------
# 结构检查（纯规则，不调用 LLM）
# ------------------------------------------------------------------

def _structure_check(data: WorkflowData) -> ReviewOutput:
    """结构检查：验证小问编号、分值一致性和标签完整性。"""
    logger.info("[structure_check] 进入结构检查节点")
    issues: list[str] = []
    draft = data.get("draft_content", "")

    # 检查题干中是否存在小问
    problem_text = data.get("problem_text", "")
    subq_in_problem = re.findall(r'\((\d+)\)', problem_text)
    if not subq_in_problem:
        issues.append("题干中未找到小问编号 (1)(2)(3)")

    # 检查解答中的分值标注
    solution_text = data.get("solution_text", "")
    scored_subqs = re.findall(r'\((\d+)\)\s*\[(\d+)分\]', solution_text)
    if solution_text and not scored_subqs:
        issues.append("解答中未找到带分值的小问标注 (N)[X分]")

    # 检查分值合计
    if scored_subqs:
        total = sum(int(s) for _, s in scored_subqs)
        expected = data.get("total_score", 0)
        if expected > 0 and total != expected:
            issues.append(f"分值合计 {total} ≠ 预期总分 {expected}")

    # 检查 block_math 标签配对
    open_tags = len(re.findall(r'<block_math\s', draft))
    close_tags = len(re.findall(r'</block_math>', draft))
    end_tags = len(re.findall(r'\\end\{block_math\}', draft))
    close_total = close_tags + end_tags
    if open_tags != close_total:
        issues.append(f"block_math 标签不配对: 开启 {open_tags} 个，闭合 {close_total} 个")

    # 检查 label 唯一性
    labels = re.findall(r'label="([^"]+)"', draft)
    dup = [l for l in set(labels) if labels.count(l) > 1]
    if dup:
        issues.append(f"重复的 label: {', '.join(dup)}")

    if issues:
        report = "【结构检查问题】\n" + "\n".join(f"- {i}" for i in issues)
    else:
        report = "【结构检查通过】无结构问题。"

    logger.info("[structure_check] 完成 | %d 个问题", len(issues))
    return {"structure_review": report}


# ------------------------------------------------------------------
# 并行入口
# ------------------------------------------------------------------

def run_reviews(data: WorkflowData) -> ReviewOutput:
    """并行执行数学检查、物理检查和结构检查。

    返回完整的 `ReviewOutput`（三个字段都有值）。
    """
    logger.info("[reviewers] 启动并行审核")

    with ThreadPoolExecutor(max_workers=3) as executor:
        math_future = executor.submit(_math_check, dict(data))
        phys_future = executor.submit(_physics_check, dict(data))
        struct_future = executor.submit(_structure_check, dict(data))

        result: ReviewOutput = {}
        result.update(math_future.result())
        result.update(phys_future.result())
        result.update(struct_future.result())

    logger.info("[reviewers] 并行审核完成")
    return result
