"""
回填器（纯 Python，不调用任何 LLM）。
将 formatted_text 中的占位符替换回原始数学公式，使用 CPHOS 模板命令。

数据归属（参见 model/state.py）：
  - 读取：LaTeXOutput.{formatted_text, formula_dict, inline_dict, figure_dict}
  - 写入：LaTeXOutput.final_latex
"""
import re

from model.state import WorkflowData, LaTeXOutput
from config.config import logger


def merge(data: WorkflowData) -> LaTeXOutput:
    """
    回填器（CPHOS 模板对齐）：
    1. 按文档顺序收集占位符，建立 label → 公式编号映射
    2. 回填 Block 公式（\\eqtagscore / \\eqtag + \\label{eq:N}）
    3. 回填 Figure 占位符 → figure 环境
    4. 回填 Inline 公式
    5. 交叉引用重映射
    6. 题干小问 → \\subq / \\subsubq / \\subsubsubq（多层级）
    7. 解答小问 → \\solsubq / \\solsubsubq / \\solsubsubsubq（多层级）
    8. 添加 \\scoring 命令
    9. 计算总分填入 \\begin{problem}[总分]
    """
    logger.info("[merge] 进入回填器")
    result = data["formatted_text"]
    formula_dict = data.get("formula_dict", {})
    inline_dict = data.get("inline_dict", {})
    figure_dict = data.get("figure_dict", {})

    # ===== Step 1: 按文档出现顺序收集占位符 =====
    block_order = re.findall(r'\{\{BLOCK_MATH_\d+\}\}', result)
    fig_order = re.findall(r'\{\{FIGURE_\d+\}\}', result)

    # ===== Step 2: 构建 label → 编号映射 =====
    label_to_eq: dict[str, int] = {}
    for eq_num, ph in enumerate(block_order, start=1):
        if ph in formula_dict:
            label_to_eq[formula_dict[ph]["label"]] = eq_num

    label_to_fig: dict[str, int] = {}
    for fig_num, ph in enumerate(fig_order, start=1):
        if ph in figure_dict:
            label_to_fig[figure_dict[ph]["label"]] = fig_num

    # ===== Step 3: 回填 Block 公式 =====
    for eq_num, ph in enumerate(block_order, start=1):
        if ph in formula_dict:
            d = formula_dict[ph]
            content = re.sub(r'\\tag\{[^}]*\}\s*', '', d["content"]).strip()
            score = d.get("score", "")
            eq_cmd = f"\\eqtagscore{{{eq_num}}}{{{score}}}" if score else f"\\eqtag{{{eq_num}}}"
            latex_block = (
                f"\n\\begin{{equation}}\n"
                f"    {content} {eq_cmd} \\label{{eq:{eq_num}}}\n"
                f"\\end{{equation}}\n"
            )
            result = result.replace(ph, latex_block, 1)

    # ===== Step 4: 回填 Figure =====
    figure_descriptions: dict[str, dict[str, str]] = {}
    for fig_num, ph in enumerate(fig_order, start=1):
        if ph in figure_dict:
            d = figure_dict[ph]
            caption = d.get("caption", "")
            fig_block = (
                f"\n%\\begin{{figure}}[H]\n"
                f"%    \\centering\n"
                f"%    \\includegraphics[width=0.4\\textwidth]{{fig/fig_{fig_num}.pdf}}\n"
                f"%    \\caption{{{caption}}}\n"
                f"%    \\label{{fig:{fig_num}}}\n"
                f"%\\end{{figure}}\n"
            )
            result = result.replace(ph, fig_block, 1)
            figure_descriptions[f"fig_{fig_num}"] = {
                "filename": f"fig_{fig_num}.pdf",
                "caption": caption,
                "description": d.get("description", ""),
            }

    # ===== Step 5: 回填 Inline 公式 =====
    for placeholder, content in inline_dict.items():
        result = result.replace(placeholder, f"${content}$")

    # ===== Step 6: 交叉引用重映射 =====
    for label, num in label_to_eq.items():
        result = result.replace(f"\\ref{{{label}}}", f"\\ref{{eq:{num}}}")
        result = result.replace(f"\\eqref{{{label}}}", f"\\eqref{{eq:{num}}}")
    for label, num in label_to_fig.items():
        result = result.replace(f"\\ref{{{label}}}", f"\\ref{{fig:{num}}}")

    # ===== Step 7: 题干小问（多层级 + Part） =====
    stmt_start = result.find("\\begin{problemstatement}")
    stmt_end = result.find("\\end{problemstatement}")
    if stmt_start >= 0 and stmt_end > stmt_start:
        stmt = result[stmt_start:stmt_end]
        stmt = re.sub(r'\n\s*([A-Z])\.\s+', r'\n\n\\pmark{\1}\\label{part:\1} ', stmt)
        stmt = re.sub(r'\n\s*\((\d+\.\d+\.\d+)\)\s+', r'\n\n\\subsubsubq{\1}\\label{q:\1} ', stmt)
        stmt = re.sub(r'\n\s*\((\d+\.\d+)\)\s+', r'\n\n\\subsubq{\1}\\label{q:\1} ', stmt)
        stmt = re.sub(r'\n\s*\((\d+)\)\s+', r'\n\n\\subq{\1}\\label{q:\1} ', stmt)
        result = result[:stmt_start] + stmt + result[stmt_end:]

    # ===== Step 8: 解答小问（多层级 + Part） =====
    sol_start = result.find("\\begin{solution}")
    sol_end = result.find("\\end{solution}")
    if sol_start >= 0 and sol_end > sol_start:
        sol = result[sol_start:sol_end]
        sol = re.sub(r'\n\s*([A-Z])\.\s*\[(\d+)\u5206\]\s*', r'\n\n\\solPart{\1}{\2}\n', sol)
        sol = re.sub(r'\n\s*\((\d+\.\d+\.\d+)\)\s*\[(\d+)\u5206\]\s*', r'\n\n\\solsubsubsubq{\1}{\2}\n', sol)
        sol = re.sub(r'\n\s*\((\d+\.\d+)\)\s*\[(\d+)\u5206\]\s*', r'\n\n\\solsubsubq{\1}{\2}\n', sol)
        sol = re.sub(r'\n\s*\((\d+)\)\s*\[(\d+)\u5206\]\s*', r'\n\n\\solsubq{\1}{\2}\n', sol)
        result = result[:sol_start] + sol + result[sol_end:]

    # ===== Step 9: 添加 \scoring =====
    if '\\scoring' not in result and '\\end{solution}' in result:
        result = result.replace('\\end{solution}', '\n\\scoring\n\\end{solution}')

    # ===== Step 10: 计算总分填入 \begin{problem}[总分] =====
    scores_part = re.findall(r'\\solPart\{[A-Z]\}\{(\d+)\}', result)
    if scores_part:
        total = sum(int(s) for s in scores_part)
    else:
        scores_l1 = re.findall(r'\\solsubq\{\d+\}\{(\d+)\}', result)
        if scores_l1:
            total = sum(int(s) for s in scores_l1)
        else:
            scores_l2 = re.findall(r'\\solsubsubq\{[^}]+\}\{(\d+)\}', result)
            scores_l3 = re.findall(r'\\solsubsubsubq\{[^}]+\}\{(\d+)\}', result)
            total = sum(int(s) for s in scores_l2) + sum(int(s) for s in scores_l3)

    if total > 0:
        def _set_total(m):
            title = m.group(1) or ""
            return f"\\begin{{problem}}[{total}]{{{title}}}"
        result = re.sub(r'\\begin\{problem\}(?:\[\d*\])?\{([^}]*)\}', _set_total, result)

    # ===== 校验 =====
    residual = []
    if "{{BLOCK_MATH_" in result:
        residual.append("BLOCK_MATH")
    if "{{INLINE_MATH_" in result:
        residual.append("INLINE_MATH")
    if "{{FIGURE_" in result:
        residual.append("FIGURE")
    if residual:
        logger.warning("[merge] 回填后仍存在残留占位符: %s", ", ".join(residual))
    else:
        logger.info("[merge] 回填完成，无残留占位符")

    return {"final_latex": result, "figure_descriptions": figure_descriptions}
