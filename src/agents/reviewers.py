"""
审核 Agent 统一入口。
包含数学检查、物理检查、结构检查和质量检查四个子 Agent，并行执行。

数据归属（参见 model/state.py）：
  - 读取：GenerationOutput.draft_content / problem_text / solution_text
    + TaskInput.total_score
  - 写入：ReviewOutput 的四个字段（math_review / physics_review / structure_review / quality_review）
    分别由四个互斥子 Agent 写入（无写冲突）。
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from model.state import WorkflowData, ReviewOutput, ReviewPatch
from model.stats import record
from client import get_client, stream_chat
from config.config import BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, logger
from prompts import load


_NUMBERED_QUESTION_RE = r'[（(](\d+(?:\.\d+)*)[）)]'


def _sum_hierarchical_scores(scored_items: list[tuple[str, str]]) -> int:
    """按小问层级求和：有父级分值时用父级，否则累加该子树下的可见子级分值。"""
    scores: dict[str, int] = {}
    for number, score in scored_items:
        scores.setdefault(number, int(score))
    if not scores:
        return 0

    children: dict[str, list[str]] = {number: [] for number in scores}
    roots: list[str] = []
    for number in scores:
        parent = None
        parts = number.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:depth])
            if candidate in scores:
                parent = candidate
                break
        if parent is None:
            roots.append(number)
        else:
            children[parent].append(number)

    def subtotal(number: str) -> int:
        if number in scores:
            return scores[number]
        return sum(subtotal(child) for child in children.get(number, []))

    return sum(subtotal(root) for root in roots)


# ------------------------------------------------------------------
# 数学检查
# ------------------------------------------------------------------

def _math_check(data: WorkflowData) -> ReviewPatch:
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

def _physics_check(data: WorkflowData) -> ReviewPatch:
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
# 质量检查
# ------------------------------------------------------------------

def _quality_check(data: WorkflowData) -> ReviewPatch:
    """质量检查：评估是否具有 CPHOS 联考试题的深度、梯度和可评分性。"""
    logger.info("[quality_check] 进入质量检查节点")
    client = get_client()

    source_material = data.get("source_material", "")
    if len(source_material) > 8000:
        source_material = source_material[:8000] + "\n\n【源材料摘录截断】"

    messages = [
        {"role": "system", "content": load("reviewers", "quality_system_prompt")},
        {"role": "user", "content": load(
            "reviewers", "quality_user_prompt",
            mode=data.get("mode", "topic_generation"),
            topic=data.get("topic", ""),
            difficulty=data.get("difficulty", ""),
            total_score=str(data.get("total_score", 0)),
            source_material=source_material,
            draft_content=data["draft_content"],
        )},
    ]

    t0 = time.time()
    content, usage = stream_chat(
        client, model=BIG_MODEL_NAME,
        messages=messages, temperature=0.0, max_tokens=BIG_MODEL_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    logger.info("[quality_check] 完成 | %d 字符 | %.0fs", len(content), elapsed)

    record(
        "quality_check", len(content), elapsed,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
    return {"quality_review": content}


# ------------------------------------------------------------------
# 结构检查（纯规则，不调用 LLM）
# ------------------------------------------------------------------

def _structure_check(data: WorkflowData) -> ReviewPatch:
    """结构检查：验证小问编号、分值一致性和标签完整性。"""
    logger.info("[structure_check] 进入结构检查节点")
    issues: list[str] = []
    notes: list[str] = []
    draft = data.get("draft_content", "")

    # 检查题干中是否存在小问
    problem_text = data.get("problem_text", "")
    subq_in_problem = re.findall(_NUMBERED_QUESTION_RE, problem_text)
    part_in_problem = re.findall(r'(?m)^\s*([A-Z])\.\s+', problem_text)
    if not subq_in_problem and not part_in_problem:
        issues.append("题干中未找到小问编号 (1)/(2)/(3)、（1）/（2）/（3）或 Part 编号 A. B. C.")

    if subq_in_problem:
        unique_numbers = list(dict.fromkeys(subq_in_problem))
        top_level = [n for n in unique_numbers if "." not in n]
        descendants = [n for n in unique_numbers if "." in n]
        leaf_count = 0
        for number in unique_numbers:
            if "." in number:
                leaf_count += 1
            elif not any(child.startswith(f"{number}.") for child in descendants):
                leaf_count += 1

        total_score = data.get("total_score", 0)
        notes.append(
            f"题量统计：总分 {total_score}，一级小问 {len(top_level)} 个，"
            f"叶子小问 {leaf_count} 个。"
        )
        if total_score <= 40 and (leaf_count > 5 or len(top_level) > 3):
            notes.append(
                "题量提示：40 分题通常以 2-3 个一级小问、3-5 个叶子小问为默认形态；"
                "若题目存在长推导或原题充实任务，可保留更多叶子小问，但质量审核需关注是否能合并同一推导链。"
            )

    # 检查解答中的分值标注
    solution_text = data.get("solution_text", "")
    scored_subqs = re.findall(rf'{_NUMBERED_QUESTION_RE}\s*\[(\d+)分\]', solution_text)
    scored_parts = re.findall(r'(?m)^\s*([A-Z])\.\s*\[(\d+)分\]', solution_text)
    if solution_text and not scored_subqs and not scored_parts:
        issues.append("解答中未找到带分值的小问标注 (N)[X分]、（N）[X分] 或 A.[X分]")

    # 检查分值合计
    if scored_parts:
        total = sum(int(s) for _, s in scored_parts)
    else:
        total = _sum_hierarchical_scores(scored_subqs)

    if scored_subqs or scored_parts:
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

    figure_labels = re.findall(r'<figure\s+label="([^"]+)"', draft)
    figure_refs = re.findall(r'\\ref\{(fig:[^}]+)\}', draft)
    missing_figs = sorted(set(figure_refs) - set(figure_labels))
    if missing_figs:
        issues.append(f"引用了未定义的图片 label: {', '.join(missing_figs)}")

    if issues:
        report = "【结构检查问题】\n" + "\n".join(f"- {i}" for i in issues)
        if notes:
            report += "\n\n【结构检查提示】\n" + "\n".join(f"- {n}" for n in notes)
    else:
        report = "【结构检查通过】无结构问题。"
        if notes:
            report += "\n\n【结构检查提示】\n" + "\n".join(f"- {n}" for n in notes)

    logger.info("[structure_check] 完成 | %d 个问题", len(issues))
    return {"structure_review": report}


# ------------------------------------------------------------------
# 并行入口
# ------------------------------------------------------------------

def run_reviews(data: WorkflowData) -> ReviewOutput:
    """并行执行数学检查、物理检查、结构检查和质量检查。

    返回完整的 `ReviewOutput`（四个字段都有值）。
    """
    logger.info("[reviewers] 启动并行审核")

    with ThreadPoolExecutor(max_workers=4) as executor:
        math_future = executor.submit(_math_check, dict(data))
        phys_future = executor.submit(_physics_check, dict(data))
        struct_future = executor.submit(_structure_check, dict(data))
        quality_future = executor.submit(_quality_check, dict(data))

        result: ReviewOutput = {
            "math_review": "",
            "physics_review": "",
            "structure_review": "",
            "quality_review": "",
        }
        result.update(math_future.result())
        result.update(phys_future.result())
        result.update(struct_future.result())
        result.update(quality_future.result())

    logger.info("[reviewers] 并行审核完成")
    return result
