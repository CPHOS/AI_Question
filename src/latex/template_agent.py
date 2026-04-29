"""
LaTeX 模板检查与调整。
先用规则检查缺失环境、分值不一致、残留占位符等问题，
再用 Agent 修复结构性排版问题。
输出 CPHOS 模板兼容的 .tex 文件。

数据归属（参见 model/state.py）：
  - 读取：LaTeXOutput.final_latex（由 merge 写入）
  - 写入：LaTeXOutput.{final_latex（覆盖）, template_report}
"""
import re

from model.state import WorkflowData, LaTeXOutput
from config.config import logger


def _rule_check(latex: str) -> list[str]:
    """规则层检查，返回问题列表。"""
    issues: list[str] = []

    # 检查必要环境
    if "\\begin{problem}" not in latex:
        issues.append("缺少 \\begin{problem} 环境")
    if "\\begin{problemstatement}" not in latex:
        issues.append("缺少 \\begin{problemstatement} 环境")
    if "\\begin{solution}" not in latex:
        issues.append("缺少 \\begin{solution} 环境")

    # 检查环境配对
    for env in ("problem", "problemstatement", "solution"):
        opens = len(re.findall(rf'\\begin\{{{env}\}}', latex))
        closes = len(re.findall(rf'\\end\{{{env}\}}', latex))
        if opens != closes:
            issues.append(f"环境不配对: \\begin{{{env}}} ({opens}) vs \\end{{{env}}} ({closes})")

    # 检查残留占位符
    for pat_name, pat in [
        ("BLOCK_MATH", r'\{\{BLOCK_MATH_\d+\}\}'),
        ("INLINE_MATH", r'\{\{INLINE_MATH_\d+\}\}'),
        ("FIGURE", r'\{\{FIGURE_\d+\}\}'),
    ]:
        matches = re.findall(pat, latex)
        if matches:
            issues.append(f"残留 {pat_name} 占位符: {len(matches)} 个")

    # 检查 \scoring 命令
    if "\\begin{solution}" in latex and "\\scoring" not in latex:
        issues.append("缺少 \\scoring 命令")

    # 检查 documentclass
    if "\\documentclass" not in latex:
        issues.append("缺少 \\documentclass 声明")

    return issues


def _auto_fix(latex: str, issues: list[str]) -> tuple[str, list[str]]:
    """尝试自动修复已知问题。返回 (修复后文本, 修复项列表)。"""
    fixes: list[str] = []

    # 补全 \scoring
    if "缺少 \\scoring 命令" in issues and "\\end{solution}" in latex:
        latex = latex.replace("\\end{solution}", "\\scoring\n\\end{solution}")
        fixes.append("补全 \\scoring 命令")

    # 补全 documentclass
    if "缺少 \\documentclass 声明" in issues:
        latex = "\\documentclass[answer]{cphos}\n\n\\begin{document}\n" + latex
        if "\\end{document}" not in latex:
            latex += "\n\\end{document}\n"
        fixes.append("补全 \\documentclass 和 document 环境")

    return latex, fixes


def fix_template(data: WorkflowData) -> LaTeXOutput:
    """
    模板修正节点：
    1. 规则检查
    2. 自动修复可修复问题
    3. 输出修正报告
    """
    logger.info("[template] 进入模板修正节点")
    latex = data.get("final_latex", "")

    issues = _rule_check(latex)

    if not issues:
        logger.info("[template] 模板检查通过，无需修正")
        return {"template_report": "模板检查通过，无需修正。"}

    logger.info("[template] 发现 %d 个问题，尝试自动修复", len(issues))
    latex, fixes = _auto_fix(latex, issues)

    # 再次检查
    remaining = _rule_check(latex)
    warnings = [i for i in remaining if i not in fixes]

    report_lines = ["模板修正报告："]
    if fixes:
        report_lines.append(f"  修复项: {'; '.join(fixes)}")
    if warnings:
        report_lines.append(f"  警告项: {'; '.join(warnings)}")
    report = "\n".join(report_lines)

    logger.info("[template] 修正完成 | 修复 %d 项 | 警告 %d 项", len(fixes), len(warnings))

    result = {"template_report": report}
    if fixes:
        result["final_latex"] = latex
    return result
