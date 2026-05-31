"""
LaTeX 模板检查与调整。
先用规则检查缺失环境、分值不一致、残留占位符等问题，
再调用外部 FormatChecker（git submodule）做格式检查，把反馈并入修正报告。
输出 CPHOS 模板兼容的 .tex 文件。

数据归属（参见 model/state.py）：
  - 读取：LaTeXOutput.final_latex（由 merge 写入）
  - 写入：LaTeXOutput.{final_latex（覆盖）, template_report}
"""
import re

from model.state import WorkflowData, LaTeXPatch
from config.config import logger
from latex.template_spec import DOCUMENTCLASS
from latex.format_checker import run_format_check, format_check_section


_EQTAG_SCORE_RE = re.compile(r'\\eqtagscore\{[^}]+\}\{\d+\}')
_ADDTEXT_SCORE_RE = re.compile(r'\\addtext\{(?P<body>(?:[^{}]|\{[^{}]*\})+)\}\{\d+\}')
_EMPTY_BRACE_GROUP_RE = re.compile(r'\{\s*\}')
_SOLUTION_MARKER_RE = re.compile(
    r'\\(?P<cmd>solPart|solsubq|solsubsubq|solsubsubsubq)\{(?P<num>[^}]+)\}\{(?P<score>\d+)\}'
)


def _normalize_latex_control_words(latex: str) -> tuple[str, list[str]]:
    """Fix compacted LaTeX control words produced by formula isolation."""
    fixes: list[str] = []
    normalized = re.sub(r'\\mathrmd(?=\b|\\|[A-Za-z_])', r'\\mathrm{d}', latex)
    if normalized != latex:
        fixes.append("normalize \\mathrmd to \\mathrm{d}")
    return normalized, fixes


def _solution_leaf_segments(sol: str) -> list[tuple[str, str]]:
    """Return solution leaf segments as (number, tex segment)."""
    hits = list(_SOLUTION_MARKER_RE.finditer(sol))
    if not hits:
        return [("", sol)] if sol.strip() else []

    level_order = {
        "solPart": 0,
        "solsubq": 1,
        "solsubsubq": 2,
        "solsubsubsubq": 3,
    }
    leaves: list[tuple[str, str]] = []
    for idx, hit in enumerate(hits):
        level = level_order[hit.group("cmd")]
        end = len(sol)
        has_child = False
        for nxt in hits[idx + 1:]:
            nxt_level = level_order[nxt.group("cmd")]
            if nxt_level <= level:
                end = nxt.start()
                break
            has_child = True
        if not has_child:
            leaves.append((hit.group("num"), sol[hit.start():end]))
    return leaves


def _has_scoring_point(tex: str) -> bool:
    if _EQTAG_SCORE_RE.search(tex):
        return True
    return any(
        _EMPTY_BRACE_GROUP_RE.sub("", match.group("body")).strip()
        for match in _ADDTEXT_SCORE_RE.finditer(tex)
    )


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

    # AI Reviewer 解析契约：problem 头、题干、解答、层级标记、评分点必须可被解析。
    if not re.search(r'\\begin\{problem\}(?:\[\d+\])?\{[^}]*\}', latex):
        issues.append("AI Reviewer 契约不满足: problem 环境缺少 [总分]{标题} 结构")
    if re.search(r'(?m)^\s*%\s*\\begin\{figure\}', latex):
        issues.append("AI Reviewer 契约不满足: figure 环境仍处于注释状态")
    if re.search(r'```|\*\*|^\s{0,3}#{1,6}\s+', latex, re.MULTILINE):
        issues.append("存在 Markdown 残留标记")
    if re.search(r'</?block_math\b|</?inline_math\b|</?figure\b', latex):
        issues.append("存在未回填的生成标签残留")

    stmt_match = re.search(r'\\begin\{problemstatement\}(.*?)\\end\{problemstatement\}', latex, re.DOTALL)
    sol_match = re.search(r'\\begin\{solution\}(.*?)\\end\{solution\}', latex, re.DOTALL)
    if stmt_match and sol_match:
        stmt = stmt_match.group(1)
        sol = sol_match.group(1)
        stmt_top = set(re.findall(r'\\subq\{([^}]+)\}', stmt))
        sol_top = set(re.findall(r'\\solsubq\{([^}]+)\}\{\d+\}', sol))
        stmt_sub = set(re.findall(r'\\subsubq\{([^}]+)\}', stmt))
        sol_sub = set(re.findall(r'\\solsubsubq\{([^}]+)\}\{\d+\}', sol))
        stmt_subsub = set(re.findall(r'\\subsubsubq\{([^}]+)\}', stmt))
        sol_subsub = set(re.findall(r'\\solsubsubsubq\{([^}]+)\}\{\d+\}', sol))
        stmt_parts = set(re.findall(r'\\pmark\{([^}]+)\}', stmt))
        sol_parts = set(re.findall(r'\\solPart\{([^}]+)\}\{\d+\}', sol))
        if not (stmt_top or stmt_parts):
            issues.append("AI Reviewer 契约不满足: 题干缺少可解析小问层级")
        if stmt_top - sol_top:
            issues.append(f"AI Reviewer 契约不满足: 解答缺少一级小问 {sorted(stmt_top - sol_top)}")
        if stmt_sub - sol_sub:
            issues.append(f"AI Reviewer 契约不满足: 解答缺少二级小问 {sorted(stmt_sub - sol_sub)}")
        if stmt_subsub - sol_subsub:
            issues.append(f"AI Reviewer 契约不满足: 解答缺少三级小问 {sorted(stmt_subsub - sol_subsub)}")
        if stmt_parts - sol_parts:
            issues.append(f"AI Reviewer 契约不满足: 解答缺少 Part {sorted(stmt_parts - sol_parts)}")
        if sol.strip() and not _has_scoring_point(sol):
            issues.append("AI Reviewer 契约不满足: 解答缺少 \\eqtagscore 或 \\addtext 评分点")
        for number, segment in _solution_leaf_segments(sol):
            if segment.strip() and not _has_scoring_point(segment):
                label = f" {number}" if number else ""
                issues.append(f"AI Reviewer 契约不满足: 叶子解答{label}缺少可解析评分点")

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
        latex = DOCUMENTCLASS + "\n\n\\begin{document}\n" + latex
        if "\\end{document}" not in latex:
            latex += "\n\\end{document}\n"
        fixes.append("补全 \\documentclass 和 document 环境")

    if "存在 Markdown 残留标记" in issues:
        latex = re.sub(r'^```\w*\s*\n?', '', latex.strip())
        latex = re.sub(r'\n?```\s*$', '', latex)
        latex = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', latex)
        latex = re.sub(r'(?<!\w)\*\*(?=\S)(.*?\S)\*\*(?!\w)', r'\1', latex)
        latex = re.sub(r'(?<!\w)__(?=\S)(.*?\S)__(?!\w)', r'\1', latex)
        fixes.append("清理 Markdown 残留标记")

    return latex, fixes


def fix_template(data: WorkflowData) -> LaTeXPatch:
    """
    模板修正节点：
    1. 规则检查
    2. 自动修复可修复问题
    3. 调用外部 FormatChecker 对修正后文本做格式检查，把反馈并入报告
    4. 输出修正报告
    """
    logger.info("[template] 进入模板修正节点")
    original = data.get("final_latex", "")
    latex, pre_fixes = _normalize_latex_control_words(original)

    issues = _rule_check(latex)

    if not issues:
        logger.info("[template] 模板检查通过，无需修正")
        report = "模板检查通过，无需修正。"
        if pre_fixes:
            report += "\n  修复项: " + "; ".join(pre_fixes)
        changed = bool(pre_fixes)
    else:
        logger.info("[template] 发现 %d 个问题，尝试自动修复", len(issues))
        latex, fixes = _auto_fix(latex, issues)
        fixes = pre_fixes + fixes

        # 再次检查
        remaining = _rule_check(latex)
        warnings = [i for i in remaining if i not in fixes]

        report_lines = ["模板修正报告："]
        if fixes:
            report_lines.append(f"  修复项: {'; '.join(fixes)}")
        if warnings:
            report_lines.append(f"  警告项: {'; '.join(warnings)}")
        report = "\n".join(report_lines)
        changed = bool(fixes)

        logger.info("[template] 修正完成 | 修复 %d 项 | 警告 %d 项", len(fixes), len(warnings))

    # 外部格式检查器反馈（优雅降级，不阻断流水线）。
    check = run_format_check(latex)
    if check.available:
        report += "\n" + format_check_section(check)
        logger.info(
            "[template] FormatChecker | 错误 %d | 警告 %d | 信息 %d",
            check.error_count, check.warning_count, check.info_count,
        )

    result: LaTeXPatch = {"template_report": report}
    if changed:
        result["final_latex"] = latex
    return result
