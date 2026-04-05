"""
回填器（纯 Python，不调用任何 LLM）。
将 formatted_text 中的占位符替换回原始数学公式。
"""
import re

from model.state import AgentState
from config.settings import logger


def python_merger(state: AgentState) -> dict:
    """
    回填器：
    1. 按文档顺序收集占位符，建立 label → 公式编号映射
    2. 回填 Block 公式（\\tag 强制编号 + \\label）
    3. 回填 Inline 公式
    4. 将 \\ref{label} 替换为硬编码编号（单次编译即可正确显示）
    5. 题干小问换行处理
    """
    logger.info("[merger] 进入回填器")
    result = state["formatted_text"]
    formula_dict = state.get("formula_dict", {})
    inline_dict = state.get("inline_dict", {})

    # ===== Step 1: 按文档出现顺序收集 Block 占位符，建立 label → 编号映射 =====
    placeholder_order = re.findall(r'\{\{BLOCK_MATH_\d+\}\}', result)
    label_to_num: dict[str, int] = {}
    for eq_num, ph in enumerate(placeholder_order, start=1):
        if ph in formula_dict:
            label_to_num[formula_dict[ph]["label"]] = eq_num

    # ===== Step 2: 回填 Block 公式 → equation 环境 + \tag 强制编号 =====
    for eq_num, ph in enumerate(placeholder_order, start=1):
        if ph in formula_dict:
            data = formula_dict[ph]
            label = data["label"]
            content = data["content"]
            content = re.sub(r'\\tag\{[^}]*\}\s*', '', content).strip()
            latex_block = (
                f"\n\\begin{{equation}}\n"
                f"\\tag{{{eq_num}}}\n"
                f"\\label{{{label}}}\n"
                f"{content}\n"
                f"\\end{{equation}}\n"
            )
            result = result.replace(ph, latex_block, 1)

    # ===== Step 3: 回填 Inline 公式 → 恢复 $...$ =====
    for placeholder, content in inline_dict.items():
        result = result.replace(placeholder, f"${content}$")

    # ===== Step 4: 将 \ref{label} 替换为硬编码编号 =====
    for label, num in label_to_num.items():
        result = result.replace(f"\\ref{{{label}}}", f"({num})")
        result = result.replace(f"\\eqref{{{label}}}", f"({num})")

    # ===== Step 5: 题干小问换行 =====
    answer_marker = "\\textbf{参考答案}"
    ans_idx = result.find(answer_marker)
    if ans_idx > 0:
        stem = result[:ans_idx]
        rest = result[ans_idx:]
        stem = re.sub(r'\n(\(\d+\))', r'\n\n\\noindent \1', stem)
        result = stem + rest

    if "{{BLOCK_MATH_" in result or "{{INLINE_MATH_" in result:
        logger.warning("[merger] 回填后仍存在残留占位符！请检查 formula_dict/inline_dict。")
    else:
        logger.info("[merger] 回填完成，无残留占位符")

    return {"final_latex": result}
