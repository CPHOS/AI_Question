"""
结构化输出模型定义。

仲裁字段使用 Literal 枚举以使 LLM 工具调用 schema 包含 enum 约束，
非法值会在 Pydantic 解析阶段直接拒绝（而非依赖运行时字符串比较兜底）。
"""
from typing import Literal

from pydantic import BaseModel, Field


# 公共枚举（其他模块可直接 import 复用，避免硬编码字符串）
ArbiterDecisionLiteral = Literal["PASS", "RETRY_PROBLEM", "RETRY_SOLUTION", "ABORT"]
ErrorCategoryLiteral = Literal["none", "style", "fatal"]


class ArbiterDecision(BaseModel):
    """仲裁结构化输出模型。通过 Function Calling 强制 LLM 输出此格式。"""
    decision: ArbiterDecisionLiteral = Field(
        description=(
            "必须严格输出 'PASS', 'RETRY_PROBLEM', 'RETRY_SOLUTION', 或 'ABORT' 四者之一。"
            "RETRY_PROBLEM: 题干需要重新设计；RETRY_SOLUTION: 仅解答需要修改。"
            "禁止输出 'RETRY' / 'PASS_WITH_EDITS' / 'NEEDS_REVISION' 等其他值。"
        )
    )
    reason: str = Field(
        description="做出该裁决的核心理由，简明扼要概括关键问题或通过原因（1-3句话）"
    )
    feedback: str = Field(
        description="综合评审意见及修改指导；若 PASS 则写'无需修改'"
    )
    error_category: ErrorCategoryLiteral = Field(
        description="错误类别: 'none'(无错误), 'style'(仅用语规范问题), 'fatal'(数学/物理/逻辑错误)"
    )


class TemplateFixReport(BaseModel):
    """LaTeX 模板修正报告。"""
    fixed: bool = Field(
        description="是否进行了修正"
    )
    fixes: list[str] = Field(
        default_factory=list,
        description="修正项列表",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="警告项列表（非阻断性问题）",
    )