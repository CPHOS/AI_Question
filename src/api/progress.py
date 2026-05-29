"""
工作流阶段进度的格式化层。

把状态机各阶段产出的 :class:`~model.state.WorkflowData` 字段，转换成**面向用户
的结构化进度快照**，而非直接暴露模型原始输出或内部流转大字典。每个阶段依据其
所属的 TypedDict（见 :mod:`model.state`）只挑选有展示意义的字段，并做：

- **防御式读取**：一律用 ``data.get(...)`` + 默认值，缺字段不抛异常；
- **长度截断**：长文本裁剪到上限并标注 ``truncated``，避免进度负载膨胀；
- **类型归一**：计数、列表、文本各按其语义整理，保证 JSON 可序列化。

设计为纯函数 + 容错：任何单个格式化函数抛错都会被 :func:`format_phase_output`
兜底为一个错误占位结构，绝不影响生成主流程。
"""
from __future__ import annotations

from typing import Any, Callable

# 单个文本字段在进度快照中的最大字符数（完整产物请走产物下载端点）。
MAX_TEXT_CHARS = 8000

# 阶段机内部 Phase.name → 面向用户的中文标签。
PHASE_LABELS: dict[str, str] = {
    "INIT": "初始化",
    "PLANNING": "命题规划",
    "PROBLEM_GENERATING": "命题生成",
    "SOLUTION_GENERATING": "解题生成",
    "REVIEWING": "多维审核",
    "ARBITRATING": "仲裁裁决",
    "FORMATTING": "LaTeX 排版",
    "TEMPLATE_FIXING": "模板修正",
    "DONE": "完成",
    "ABORTED": "已终止",
    "ERROR": "执行出错",
}

# 规范的阶段展示顺序（重试时阶段会重复出现，按 seq 区分）。
PHASE_ORDER: list[str] = [
    "PLANNING", "PROBLEM_GENERATING", "SOLUTION_GENERATING",
    "REVIEWING", "ARBITRATING", "FORMATTING", "TEMPLATE_FIXING", "DONE",
]


def phase_label(phase: str) -> str:
    """返回阶段的中文标签（未知阶段回退为原名）。"""
    return PHASE_LABELS.get(phase, phase)


def _clip(value: Any) -> dict[str, Any]:
    """把任意值规整为带截断信息的文本结构。"""
    text = "" if value is None else str(value)
    full = len(text)
    truncated = full > MAX_TEXT_CHARS
    return {
        "text": text[:MAX_TEXT_CHARS],
        "truncated": truncated,
        "length": full,
    }


def _count(value: Any) -> int:
    """安全地返回容器长度（非容器回退为 0）。"""
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return 0


# ---------------------------------------------------------------------------
# 各阶段格式化器：data -> 结构化快照
# ---------------------------------------------------------------------------

def _fmt_planning(data: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "planning", "planning_notes": _clip(data.get("planning_notes", ""))}


def _fmt_problem(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "problem",
        "title": str(data.get("title", "")),
        "problem_text": _clip(data.get("problem_text", "")),
    }


def _fmt_solution(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "solution",
        "solution_text": _clip(data.get("solution_text", "")),
    }


_REVIEWERS = (
    ("math", "数学审核", "math_review"),
    ("physics", "物理审核", "physics_review"),
    ("structure", "结构审核", "structure_review"),
    ("quality", "质量审核", "quality_review"),
)


def _fmt_review(data: dict[str, Any]) -> dict[str, Any]:
    reviews = []
    for key, label, field in _REVIEWERS:
        opinion = data.get(field, "") or ""
        reviews.append({
            "reviewer": key,
            "label": label,
            "empty": not bool(opinion.strip()),
            "opinion": _clip(opinion),
        })
    return {"kind": "review", "reviews": reviews}


def _fmt_arbitration(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "arbitration",
        "decision": str(data.get("arbiter_decision", "")),
        "error_category": str(data.get("error_category", "")),
        "reason": _clip(data.get("arbiter_reason", "")),
        "feedback": _clip(data.get("arbiter_feedback", "")),
        "retry": {
            "problem": int(data.get("problem_retry_count", 0) or 0),
            "solution": int(data.get("solution_retry_count", 0) or 0),
            "total": int(data.get("retry_count", 0) or 0),
        },
    }


def _fmt_formatting(data: dict[str, Any]) -> dict[str, Any]:
    # 仅展示统计与产物规模，完整 LaTeX 走产物下载端点。
    return {
        "kind": "formatting",
        "block_formula_count": _count(data.get("formula_dict", {})),
        "inline_formula_count": _count(data.get("inline_dict", {})),
        "figure_count": _count(data.get("figure_dict", {})),
        "final_latex_chars": len(str(data.get("final_latex", ""))),
    }


def _fmt_template_fixing(data: dict[str, Any]) -> dict[str, Any]:
    figures = data.get("figure_descriptions", {})
    keys = list(figures.keys()) if isinstance(figures, dict) else []
    return {
        "kind": "template_fixing",
        "template_report": _clip(data.get("template_report", "")),
        "figure_count": len(keys),
        "figure_keys": keys,
    }


def _fmt_done(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "done",
        "title": str(data.get("title", "")),
        "decision": str(data.get("arbiter_decision", "")),
        "final_latex_chars": len(str(data.get("final_latex", ""))),
    }


def _fmt_aborted(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "aborted",
        "decision": str(data.get("arbiter_decision", "")),
        "reason": _clip(data.get("arbiter_reason", "")),
    }


_FORMATTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "PLANNING": _fmt_planning,
    "PROBLEM_GENERATING": _fmt_problem,
    "SOLUTION_GENERATING": _fmt_solution,
    "REVIEWING": _fmt_review,
    "ARBITRATING": _fmt_arbitration,
    "FORMATTING": _fmt_formatting,
    "TEMPLATE_FIXING": _fmt_template_fixing,
    "DONE": _fmt_done,
    "ABORTED": _fmt_aborted,
}


def format_phase_output(phase: str, data: dict[str, Any]) -> dict[str, Any]:
    """把某阶段完成时的工作流数据格式化为结构化进度快照。

    Args:
        phase: 阶段名（:class:`engine.state_machine.Phase` 的 ``name``）。
        data: 当前工作流数据字典。

    Returns:
        JSON 可序列化的结构化快照；无对应格式化器时返回通用占位，
        任意异常被兜底为 ``{"kind": "error", ...}``，绝不向上抛出。
    """
    formatter = _FORMATTERS.get(phase)
    if formatter is None:
        return {"kind": "generic", "phase": phase}
    try:
        return formatter(data or {})
    except Exception as exc:  # noqa: BLE001 - 进度格式化绝不影响主流程
        return {"kind": "error", "phase": phase, "message": f"{type(exc).__name__}: {exc}"}
