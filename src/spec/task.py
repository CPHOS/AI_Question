"""
输入规格与难度规划数据模型。

QuestionMode:  命题模式枚举
DifficultyProfile: 三维难度目标
TaskSpec:      统一输入规格，CLI / JSON 均归一到此结构
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class QuestionMode(str, Enum):
    """命题模式。

    所有模式共用同一条 workflow，差异仅体现在 TaskSpec
    和 planning prompt 中。
    """
    TOPIC_GENERATION = "topic_generation"            # 主题生成（自由命题）
    LITERATURE_ADAPTATION = "literature_adaptation"  # 文献改编
    IDEA_EXPANSION = "idea_expansion"                # 思路拓展
    PROBLEM_ENRICHMENT = "problem_enrichment"        # 简单题丰富


class DifficultyProfile(BaseModel):
    """三维难度目标（沿用 AI_Reviewer 语义）。"""
    target_computation: int = Field(
        default=6, ge=1, le=10,
        description="计算难度目标 (1-10)",
    )
    target_thinking: int = Field(
        default=6, ge=1, le=10,
        description="思维难度目标 (1-10)",
    )
    target_overall: int = Field(
        default=7, ge=1, le=10,
        description="综合难度目标 (1-10)",
    )
    question_count: int = Field(
        default=4, ge=2, le=6,
        description="预期小问数量",
    )
    score_distribution: list[int] = Field(
        default_factory=list,
        description="各小问建议分值分配（可为空，由 planner 生成）",
    )


class TaskSpec(BaseModel):
    """统一输入规格。

    CLI、JSON 文件等不同来源经 normalizer 归一后均为此结构。
    """
    mode: QuestionMode = Field(
        default=QuestionMode.TOPIC_GENERATION,
        description="命题模式",
    )
    topic: str = Field(
        default="",
        description="物理主题（主题生成模式必填）",
    )
    source_material: str = Field(
        default="",
        description="源材料：文献摘要、原题内容或思路描述（改编类模式使用）",
    )
    difficulty: str = Field(
        default="国家集训队",
        description="难度等级描述",
    )
    total_score: int = Field(
        default=40, ge=20, le=80,
        description="题目总分（CPhO 决赛单题主流分值为 40）",
    )
    difficulty_profile: DifficultyProfile = Field(
        default_factory=DifficultyProfile,
        description="三维难度目标",
    )
