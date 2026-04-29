"""
输入规格化器。
将 CLI 参数或 JSON 文件统一转换为 TaskSpec → WorkflowData 初始状态。

本模块负责"零号阶段"的工作：把外部输入（CLI / JSON）规格化为
`TaskInput`（参见 model/state.py），并补齐其他阶段记录的空初始字段，
组装出后续状态机可以直接 `update()` 的完整 `WorkflowData`。
"""
from __future__ import annotations

import json
from pathlib import Path

from spec.task import TaskSpec, QuestionMode, DifficultyProfile
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
    if total_score < 40:
        return DifficultyProfile(
            target_computation=5, target_thinking=5, target_overall=5,
            question_count=3, score_distribution=[],
        )
    elif total_score <= 60:
        return DifficultyProfile(
            target_computation=6, target_thinking=7, target_overall=7,
            question_count=4, score_distribution=[],
        )
    else:
        return DifficultyProfile(
            target_computation=7, target_thinking=8, target_overall=8,
            question_count=5, score_distribution=[],
        )


def from_cli(
    *,
    topic: str = "",
    difficulty: str = "国家集训队",
    total_score: int = 40,
    source_file: str | None = None,
    mode: str | None = None,
) -> WorkflowData:
    """从 CLI 参数构造 WorkflowData。"""
    # 模式推断
    if mode:
        question_mode = QuestionMode(mode)
    elif source_file:
        question_mode = QuestionMode.LITERATURE_ADAPTATION
    else:
        question_mode = QuestionMode.TOPIC_GENERATION

    source_material = ""
    if source_file:
        p = Path(source_file)
        if not p.exists():
            raise FileNotFoundError(f"源材料文件未找到: {p}")
        source_material = p.read_text(encoding="utf-8")
        if not topic:
            topic = p.stem

    spec = TaskSpec(
        mode=question_mode,
        topic=topic,
        source_material=source_material,
        difficulty=difficulty,
        total_score=total_score,
        difficulty_profile=_infer_difficulty_profile(total_score),
    )

    logger.info("[normalizer] 输入规格化完成 | mode=%s topic=%s score=%d",
                spec.mode.value, spec.topic[:40], spec.total_score)

    return _spec_to_workflow_data(spec)


def from_json(filepath: str) -> WorkflowData:
    """从 JSON 文件加载任务。"""
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"任务文件未找到: {p}")

    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if "topic" not in raw and "source_material" not in raw:
        raise KeyError(f"JSON 缺少 'topic' 或 'source_material' 字段，文件: {p}")

    mode_str = raw.get("mode", "topic_generation")
    difficulty_profile = raw.get("difficulty_profile", {})

    spec = TaskSpec(
        mode=QuestionMode(mode_str),
        topic=raw.get("topic", ""),
        source_material=raw.get("source_material", ""),
        difficulty=raw.get("difficulty", "国家集训队"),
        total_score=raw.get("total_score", 40),
        difficulty_profile=DifficultyProfile(**difficulty_profile) if difficulty_profile
        else _infer_difficulty_profile(raw.get("total_score", 40)),
    )

    logger.info("[normalizer] JSON 输入规格化完成 | mode=%s topic=%s",
                spec.mode.value, spec.topic[:40])

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
    return data
