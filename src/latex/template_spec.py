"""
CPHOS LaTeX 模板词汇表（单一事实来源）。

命题流程多处需要与 ``CPHOS-Latex`` 模板约定保持一致：插图宽度、文档类声明等。
这些字面量历史上散落在 :mod:`latex.merge`、:mod:`app.outputs`、
:mod:`latex.template_agent` 等文件中，一旦模板升级极易遗漏其一。此处集中声明，
供各处统一引用。

注意：模板**校验**用的解析正则（如检测 ``\\documentclass`` / ``\\scoring`` 是否存在）
仍保留在 :mod:`latex.template_agent` 内，因其语义是「宽松匹配」而非「精确等值」，
与此处的「精确生成模板」用途不同。
"""
from __future__ import annotations

# 插图默认宽度（merge 阶段生成 figure 环境、outputs 阶段生成缺图占位框时共用）。
FIGURE_WIDTH: str = r"0.55\textwidth"

# CPHOS 答案模式文档类声明（补全缺失 \documentclass 时注入）。
DOCUMENTCLASS: str = r"\documentclass[answer]{cphos}"
