"""model 包 — 数据模型、状态定义与运行统计。"""
from model.state import (
    WorkflowData,
    TaskInput,
    GenerationOutput,
    ReviewOutput,
    ArbitrationOutput,
    LaTeXOutput,
)
from model.schema import (
    ArbiterDecision,
    TemplateFixReport,
    ArbiterDecisionLiteral,
    ErrorCategoryLiteral,
)
from model.stats import record, get_all, get_total_tokens, clear

__all__ = [
    # 整体视图
    "WorkflowData",
    # 各阶段记录
    "TaskInput",
    "GenerationOutput",
    "ReviewOutput",
    "ArbitrationOutput",
    "LaTeXOutput",
    # 结构化模型
    "ArbiterDecision",
    "TemplateFixReport",
    "ArbiterDecisionLiteral",
    "ErrorCategoryLiteral",
    # 统计
    "record",
    "get_all",
    "get_total_tokens",
    "clear",
]
