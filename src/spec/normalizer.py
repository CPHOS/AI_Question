"""
输入规格化器。

将后端 API 请求参数统一转换为 TaskSpec → WorkflowData 初始状态。

本模块负责"零号阶段"的工作：把外部输入规格化为
`TaskInput`（参见 model/state.py），并补齐其他阶段记录的空初始字段，
组装出后续状态机可以直接 `update()` 的完整 `WorkflowData`。
"""
from __future__ import annotations

from spec.task import (
    TaskSpec, QuestionMode, DifficultyProfile,
    DEFAULT_DIFFICULTY, DEFAULT_TOTAL_SCORE, SCORE_TIER_LOW, SCORE_TIER_MID,
)
from model.state import (
    WorkflowData,
    TaskInput,
    GenerationOutput,
    ReviewOutput,
    ArbitrationOutput,
    LaTeXOutput,
)
from config.config import logger


def _infer_difficulty_profile(total_score: int) -> DifficultyProfile:
    """根据总分推导默认难度规划。"""
    if total_score < SCORE_TIER_LOW:
        return DifficultyProfile(
            target_computation=5, target_thinking=5, target_overall=5,
            question_count=2, score_distribution=[],
        )
    elif total_score <= SCORE_TIER_MID:
        return DifficultyProfile(
            target_computation=6, target_thinking=7, target_overall=7,
            question_count=3, score_distribution=[],
        )
    else:
        return DifficultyProfile(
            target_computation=7, target_thinking=8, target_overall=8,
            question_count=5, score_distribution=[],
        )


def from_api(
    *,
    topic: str = "",
    source_material: str = "",
    difficulty: str = DEFAULT_DIFFICULTY,
    total_score: int = DEFAULT_TOTAL_SCORE,
    mode: str | None = None,
) -> WorkflowData:
    """从 API 请求参数构造 WorkflowData。

    源材料以文本直接传入（而非文件路径），适配后端 HTTP 提交场景。

    Args:
        topic: 物理主题。
        source_material: 源材料文本（改编类模式使用）。
        difficulty: 难度等级描述。
        total_score: 题目总分。
        mode: 命题模式；留空时按是否提供 source_material 推断。

    Returns:
        可供状态机直接执行的初始 :class:`~model.state.WorkflowData`。
    """
    if mode:
        question_mode = QuestionMode(mode)
    elif source_material:
        question_mode = QuestionMode.LITERATURE_ADAPTATION
    else:
        question_mode = QuestionMode.TOPIC_GENERATION

    spec = TaskSpec(
        mode=question_mode,
        topic=topic,
        source_material=source_material,
        difficulty=difficulty,
        total_score=total_score,
        difficulty_profile=_infer_difficulty_profile(total_score),
    )

    logger.info("[normalizer] API 输入规格化完成 | mode=%s topic=%s score=%d",
                spec.mode.value, spec.topic[:40], spec.total_score)

    return _spec_to_workflow_data(spec)


def _spec_to_workflow_data(spec: TaskSpec) -> WorkflowData:
    """将 TaskSpec 展开为 WorkflowData 初始状态。

    分阶段拼装：每个阶段记录在此处给出"零值"初始状态，后续阶段 Agent
    通过 `data.update(stage_output)` 写入自己阶段的实际产物。
    """
    initial_task: TaskInput = {
        "mode": spec.mode.value,
        "topic": spec.topic,
        "source_material": spec.source_material,
        "difficulty": spec.difficulty,
        "total_score": spec.total_score,
        "difficulty_profile": spec.difficulty_profile.model_dump(),
        "planning_notes": "",
    }
    initial_generation: GenerationOutput = {
        "title": "",
        "problem_text": "",
        "solution_text": "",
        "draft_content": "",
    }
    initial_review: ReviewOutput = {
        "math_review": "",
        "physics_review": "",
        "structure_review": "",
        "quality_review": "",
    }
    initial_arbitration: ArbitrationOutput = {
        "arbiter_decision": "",
        "arbiter_feedback": "",
        "arbiter_reason": "",
        "error_category": "",
        "retry_count": 0,
        "problem_retry_count": 0,
        "solution_retry_count": 0,
    }
    initial_latex: LaTeXOutput = {
        "formula_dict": {},
        "inline_dict": {},
        "figure_dict": {},
        "tagged_text": "",
        "formatted_text": "",
        "final_latex": "",
        "template_report": "",
        "figure_descriptions": {},
    }

    data: WorkflowData = {}
    data.update(initial_task)
    data.update(initial_generation)
    data.update(initial_review)
    data.update(initial_arbitration)
    data.update(initial_latex)
    required_keys = (
        "mode",
        "topic",
        "source_material",
        "difficulty",
        "total_score",
        "difficulty_profile",
        "planning_notes",
        "title",
        "problem_text",
        "solution_text",
        "draft_content",
        "math_review",
        "physics_review",
        "structure_review",
        "quality_review",
        "arbiter_decision",
        "arbiter_feedback",
        "arbiter_reason",
        "error_category",
        "retry_count",
        "problem_retry_count",
        "solution_retry_count",
        "formula_dict",
        "inline_dict",
        "figure_dict",
        "tagged_text",
        "formatted_text",
        "final_latex",
        "template_report",
        "figure_descriptions",
    )
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise AssertionError(f"WorkflowData 初始化缺少关键字段: {missing}")
    return data
