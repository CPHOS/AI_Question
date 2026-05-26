"""
回填器（纯 Python，不调用任何 LLM）。
将 formatted_text 中的占位符替换回原始数学公式，使用 CPHOS 模板命令。

数据归属（参见 model/state.py）：
  - 读取：LaTeXOutput.{formatted_text, formula_dict, inline_dict, figure_dict}
  - 写入：LaTeXOutput.final_latex
"""
import re

from model.state import WorkflowData, LaTeXPatch
from config.config import logger


_NUMBERED_QUESTION_RE = r'[（(](\d+(?:\.\d+)*)[）)]'


def _collect_placeholder_order(text: str, prefix: str) -> list[str]:
    """按文档出现顺序收集指定类型占位符。"""
    return re.findall(rf'\{{\{{{prefix}_\d+\}}\}}', text)


def _build_label_map(
    placeholders: list[str],
    source: dict,
    *,
    label_key: str = "label",
) -> dict[str, int]:
    """构建原始 label 到输出编号的映射。"""
    label_map: dict[str, int] = {}
    for index, placeholder in enumerate(placeholders, start=1):
        item = source.get(placeholder)
        if item and item.get(label_key):
            label_map[item[label_key]] = index
    return label_map


def _escape_latex_text(text: str) -> str:
    """转义普通 LaTeX 文本中的特殊字符。"""
    mapping = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(mapping.get(ch, ch) for ch in text)


def _find_duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _sum_hierarchical_scores(scored_items: list[tuple[str, str]]) -> int:
    """按小问层级求和：有父级分值时用父级，否则累加该子树下的可见子级分值。"""
    duplicates = _find_duplicates([number for number, _ in scored_items])
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


def _merge_block_math(text: str, block_order: list[str], formula_dict: dict) -> str:
    """回填 block 公式占位符。"""
    result = text
    for eq_num, placeholder in enumerate(block_order, start=1):
        item = formula_dict.get(placeholder)
        if not item:
            continue
        content = re.sub(r'\\tag\{[^}]*\}\s*', '', item["content"]).strip()
        score = item.get("score", "")
        eq_cmd = f"\\eqtagscore{{{eq_num}}}{{{score}}}" if score else f"\\eqtag{{{eq_num}}}"
        latex_block = (
            f"\n\\begin{{equation}}\n"
            f"    {content} {eq_cmd} \\label{{eq:{eq_num}}}\n"
            f"\\end{{equation}}\n"
        )
        result = result.replace(placeholder, latex_block, 1)
    return result


def _merge_figures(text: str, fig_order: list[str], figure_dict: dict) -> tuple[str, dict]:
    """回填 figure 占位符，并导出绘图需求元数据。

    题面始终引用外置 PDF。具体路径在输出阶段按 task_id 改写为
    `<task_id>_assets/figN.pdf`，使最终 tex 可直接交给 AI Reviewer 和
    官方 cphos 模板编译。
    """
    result = text
    figure_descriptions: dict[str, dict[str, str]] = {}
    for fig_num, placeholder in enumerate(fig_order, start=1):
        item = figure_dict.get(placeholder)
        if not item:
            continue
        caption = _escape_latex_text(item.get("caption", ""))
        filename = f"fig{fig_num}.pdf"
        tikz_filename = f"fig{fig_num}.tex"
        fig_block = (
            f"\n\\begin{{figure}}[H]\n"
            f"    \\centering\n"
            f"    \\includegraphics[width=0.55\\textwidth]{{fig/{filename}}}\n"
            f"    \\caption{{{caption}}}\n"
            f"    \\label{{fig:{fig_num}}}\n"
            f"\\end{{figure}}\n"
        )
        result = result.replace(placeholder, fig_block, 1)
        figure_descriptions[f"fig_{fig_num}"] = {
            "filename": filename,
            "tikz_filename": tikz_filename,
            "caption": item.get("caption", ""),
            "description": item.get("description", ""),
        }
    return result, figure_descriptions


def _merge_inline_math(text: str, inline_dict: dict) -> str:
    """回填 inline 公式占位符。"""
    result = text
    for placeholder, content in inline_dict.items():
        result = result.replace(placeholder, f"${content}$")
    return result


def _sanitize_markdown_markup(text: str) -> str:
    """清理 LLM 残留的 Markdown 标记，保留 LaTeX 结构。"""
    result = text.strip()
    result = re.sub(r'^```\w*\s*\n?', '', result)
    result = re.sub(r'\n?```\s*$', '', result)
    result = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', result)
    result = re.sub(r'(?m)^(\s*)\*(提示[:：].*?)\*\s*$', r'\1\2', result)
    result = re.sub(r'(?<!\w)\*\*(?=\S)(.*?\S)\*\*(?!\w)', r'\1', result)
    result = re.sub(r'(?<!\w)__(?=\S)(.*?\S)__(?!\w)', r'\1', result)
    return result


def _display_standalone_inline_math(text: str) -> str:
    """把独占一行的行内公式转为展示公式，减少题解中的长行内公式。"""
    return re.sub(
        r'(?m)^(?P<indent>\s*)\$(?P<body>[^$\n]+)\$\s*$',
        lambda m: f"{m.group('indent')}\\[\n{m.group('body').strip()}\n{m.group('indent')}\\]",
        text,
    )


def _rewrite_references(
    text: str,
    label_to_eq: dict[str, int],
    label_to_fig: dict[str, int],
) -> str:
    """将原始 label 引用改写为输出编号引用。"""
    result = text
    for label, num in label_to_eq.items():
        result = result.replace(f"\\ref{{{label}}}", f"\\ref{{eq:{num}}}")
        result = result.replace(f"\\eqref{{{label}}}", f"\\eqref{{eq:{num}}}")
    for label, num in label_to_fig.items():
        result = result.replace(f"\\ref{{{label}}}", f"\\ref{{fig:{num}}}")
    return result


def _rewrite_problem_questions(text: str) -> str:
    """改写题干中的 Part 与多层级小问命令。"""
    stmt_start = text.find("\\begin{problemstatement}")
    stmt_end = text.find("\\end{problemstatement}")
    if stmt_start < 0 or stmt_end <= stmt_start:
        return text

    stmt = text[stmt_start:stmt_end]
    lines: list[str] = []
    current_part = ""

    def _append_paragraph(line: str) -> None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)

    def _cmd_for(number: str) -> str:
        depth = number.count(".")
        if depth == 0:
            return "subq"
        if depth == 1:
            return "subsubq"
        return "subsubsubq"

    for line in stmt.splitlines():
        stripped = line.strip()
        part_match = re.match(r'^\*\*([A-Z])\.\s*(.*?)\*\*\s*$', stripped)
        if not part_match:
            part_match = re.match(r'^([A-Z])\.\s+(.*?（?\(?\d+\s*分[）\)]?)\s*$', stripped)
        if part_match:
            current_part = part_match.group(1)
            rest = part_match.group(2).strip()
            _append_paragraph(f"\\pmark{{{current_part}}}\\label{{part:{current_part}}} {rest}")
            continue

        q_match = re.match(rf'^\*{{0,2}}{_NUMBERED_QUESTION_RE}\s*(.*?)\*{{0,2}}$', stripped)
        if q_match:
            number = q_match.group(1)
            rest = q_match.group(2)
            label_number = f"{current_part}.{number}" if current_part else number
            command = _cmd_for(number)
            _append_paragraph(f"\\{command}{{{number}}}\\label{{q:{label_number}}} {rest}")
            continue

        lines.append(line)

    stmt = "\n".join(lines).rstrip() + "\n"
    return text[:stmt_start] + stmt + text[stmt_end:]


def _rewrite_solution_questions(text: str) -> str:
    """改写解答中的 Part 与多层级小问评分命令。"""
    sol_start = text.find("\\begin{solution}")
    sol_end = text.find("\\end{solution}")
    if sol_start < 0 or sol_end <= sol_start:
        return text

    sol = text[sol_start:sol_end]
    lines: list[str] = []

    def _append_paragraph(line: str) -> None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)

    def _cmd_for(number: str) -> str:
        depth = number.count(".")
        if depth == 0:
            return "solsubq"
        if depth == 1:
            return "solsubsubq"
        return "solsubsubsubq"

    for line in sol.splitlines():
        stripped = line.strip()
        part_match = re.match(r'^\*\*([A-Z])\.\s*.*?\((\d+)\s*分\)\*\*\s*$', stripped)
        if not part_match:
            part_match = re.match(r'^([A-Z])\.\s*\[(\d+)分\]\s*$', stripped)
        if not part_match:
            part_match = re.match(r'^([A-Z])\.\s+.*?\((\d+)\s*分\)\s*$', stripped)
        if part_match:
            _append_paragraph(f"\\solPart{{{part_match.group(1)}}}{{{part_match.group(2)}}}")
            continue

        q_match = re.match(rf'^\*{{0,2}}{_NUMBERED_QUESTION_RE}\s*\[(\d+)分\]\s*(.*?)\*{{0,2}}$', stripped)
        if q_match:
            command = _cmd_for(q_match.group(1))
            rest = q_match.group(3)
            suffix = f" {rest}" if rest else ""
            _append_paragraph(f"\\{command}{{{q_match.group(1)}}}{{{q_match.group(2)}}}{suffix}")
            continue

        lines.append(line)

    sol = "\n".join(lines).rstrip() + "\n"
    return text[:sol_start] + sol + text[sol_end:]


def _ensure_scoring(text: str) -> str:
    """确保 solution 环境内存在评分区命令。"""
    if '\\scoring' not in text and '\\end{solution}' in text:
        return text.replace('\\end{solution}', '\n\\scoring\n\\end{solution}')
    return text


def _insert_missing_solution_parent_headers(text: str) -> str:
    """为仅含子级解答的小问补齐父级解答标题，满足外部解析器层级契约。"""
    sol_start = text.find("\\begin{solution}")
    sol_end = text.find("\\end{solution}")
    if sol_start < 0 or sol_end <= sol_start:
        return text

    sol = text[sol_start:sol_end]
    marker_re = re.compile(
        r'\\(?P<cmd>solsubq|solsubsubq|solsubsubsubq)'
        r'\{(?P<number>\d+(?:\.\d+)*)\}\{(?P<score>\d+)\}'
    )
    explicit_scores: dict[str, int] = {
        match.group("number"): int(match.group("score"))
        for match in marker_re.finditer(sol)
    }
    if not explicit_scores:
        return text

    all_numbers = set(explicit_scores)
    for number in list(explicit_scores):
        parts = number.split(".")
        for depth in range(1, len(parts)):
            all_numbers.add(".".join(parts[:depth]))

    children: dict[str, list[str]] = {number: [] for number in all_numbers}
    for number in all_numbers:
        parts = number.split(".")
        if len(parts) == 1:
            continue
        parent = ".".join(parts[:-1])
        if parent in children:
            children[parent].append(number)

    def _score(number: str) -> int:
        if number in explicit_scores:
            return explicit_scores[number]
        return sum(_score(child) for child in children.get(number, []))

    def _cmd_for(number: str) -> str:
        depth = number.count(".")
        if depth == 0:
            return "solsubq"
        if depth == 1:
            return "solsubsubq"
        return "solsubsubsubq"

    def repl(match: re.Match) -> str:
        number = match.group("number")
        inserts: list[str] = []
        parts = number.split(".")
        for depth in range(1, len(parts)):
            ancestor = ".".join(parts[:depth])
            if ancestor in explicit_scores:
                continue
            score = _score(ancestor)
            if score <= 0:
                continue
            explicit_scores[ancestor] = score
            inserts.append(f"\\{_cmd_for(ancestor)}{{{ancestor}}}{{{score}}}")
        if inserts:
            return "\n\n".join(inserts + [match.group(0)])
        return match.group(0)

    sol = marker_re.sub(repl, sol)
    return text[:sol_start] + sol + text[sol_end:]


def _compute_total_score(text: str) -> int:
    """从解答评分命令中计算总分。"""
    scores_part = re.findall(r'\\solPart\{([A-Z])\}\{(\d+)\}', text)
    if scores_part:
        duplicate_parts = _find_duplicates([part for part, _ in scores_part])
        if duplicate_parts:
            raise ValueError(f"重复的 Part 评分编号: {', '.join(duplicate_parts)}")
        return sum(int(score) for _, score in scores_part)

    scored_items = re.findall(r'\\solsub(?:sub(?:sub)?)?q\{(\d+(?:\.\d+)*)\}\{(\d+)\}', text)
    return _sum_hierarchical_scores(scored_items)


def _fill_problem_total(text: str, total: int) -> str:
    """将计算出的总分写入 problem 环境。"""
    if total <= 0:
        return text

    def _set_total(match):
        title = re.sub(r'(?<!\\)_', r'\\_', match.group(1) or "")
        return f"\\begin{{problem}}[{total}]{{{title}}}"

    return re.sub(r'\\begin\{problem\}(?:\[\d*\])?\{([^}]*)\}', _set_total, text)


def _residual_placeholders(text: str) -> list[str]:
    """返回回填后仍残留的占位符类型。"""
    residual = []
    if "{{BLOCK_MATH_" in text:
        residual.append("BLOCK_MATH")
    if "{{INLINE_MATH_" in text:
        residual.append("INLINE_MATH")
    if "{{FIGURE_" in text:
        residual.append("FIGURE")
    return residual


def merge(data: WorkflowData) -> LaTeXPatch:
    """
    回填器（CPHOS 模板对齐）。

    `merge` 保持顺序编排职责；具体规则由本文件内的纯函数承载，便于
    后续扩展 RuleManager 或单独测试某条回填规则。
    """
    logger.info("[merge] 进入回填器")
    result = data["formatted_text"]
    formula_dict = data.get("formula_dict", {})
    inline_dict = data.get("inline_dict", {})
    figure_dict = data.get("figure_dict", {})

    block_order = _collect_placeholder_order(result, "BLOCK_MATH")
    fig_order = _collect_placeholder_order(result, "FIGURE")
    label_to_eq = _build_label_map(block_order, formula_dict)
    label_to_fig = _build_label_map(fig_order, figure_dict)

    result = _merge_block_math(result, block_order, formula_dict)
    result, figure_descriptions = _merge_figures(result, fig_order, figure_dict)
    result = _merge_inline_math(result, inline_dict)
    result = _display_standalone_inline_math(result)
    result = _rewrite_references(result, label_to_eq, label_to_fig)
    result = _rewrite_problem_questions(result)
    result = _rewrite_solution_questions(result)
    result = _insert_missing_solution_parent_headers(result)
    result = _sanitize_markdown_markup(result)
    result = _ensure_scoring(result)
    result = _fill_problem_total(result, _compute_total_score(result))

    residual = _residual_placeholders(result)
    if residual:
        logger.warning("[merge] 回填后仍存在残留占位符: %s", ", ".join(residual))
    else:
        logger.info("[merge] 回填完成，无残留占位符")

    return {"final_latex": result, "figure_descriptions": figure_descriptions}
