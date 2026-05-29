"""
小问层级评分与编号工具（纯函数，不依赖 LLM）。

本模块是「小问编号识别 + 重复检测 + 层级分值求和」的单一事实来源，
供回填器（:mod:`latex.merge`）与结构审核（:mod:`agents.reviewers`）共用，
避免同一套规则在多处复制粘贴造成漂移。
"""
from __future__ import annotations

# 小问编号正则：匹配中英文括号包裹的多级编号，如 (1) / （2.3） / (1.2.1)。
NUMBERED_QUESTION_RE = r'[（(](\d+(?:\.\d+)*)[）)]'


def find_duplicates(values: list[str]) -> list[str]:
    """按首次重复出现顺序返回重复值（每个重复值只列一次）。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def sum_hierarchical_scores(scored_items: list[tuple[str, str]]) -> int:
    """按小问层级求和：有父级分值时用父级，否则累加该子树下的可见子级分值。

    Args:
        scored_items: ``(编号, 分值字符串)`` 列表，编号形如 ``"1"`` / ``"1.2"``。

    Raises:
        ValueError: 存在重复的小问编号时（无法确定层级归属）。
    """
    duplicates = find_duplicates([number for number, _ in scored_items])
    if duplicates:
        raise ValueError(f"重复的小问评分编号: {', '.join(duplicates)}")

    scores: dict[str, int] = {}
    for number, score in scored_items:
        scores[number] = int(score)
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
