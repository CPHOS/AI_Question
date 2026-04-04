"""
Node 6: 回填器（纯 Python，不调用任何 LLM）。
将 formatted_text 中的占位符替换回原始数学公式。
Block 公式回填为 \\begin{equation} + \\label 环境。
Inline 公式回填为 $...$ 行内格式。
"""
from state.schema import AgentState
from config.settings import logger


def python_merger(state: AgentState) -> dict:
    """
    回填器：
    1. 遍历 formula_dict，将 {{BLOCK_MATH_X}} 替换为 LaTeX equation 环境
    2. 遍历 inline_dict，将 {{INLINE_MATH_X}} 替换为 $...$
    """
    logger.info("[merger] 进入回填器")
    result = state["formatted_text"]

    # 回填 Block 公式 → 包裹在 equation 环境中
    for placeholder, data in state.get("formula_dict", {}).items():
        label = data["label"]
        content = data["content"]
        latex_block = (
            f"\n\\begin{{equation}}\n"
            f"\\label{{{label}}}\n"
            f"{content}\n"
            f"\\end{{equation}}\n"
        )
        result = result.replace(placeholder, latex_block)

    # 回填 Inline 公式 → 恢复 $...$
    for placeholder, content in state.get("inline_dict", {}).items():
        result = result.replace(placeholder, f"${content}$")

    # 最终完整性检查：确认无残留占位符
    if "{{BLOCK_MATH_" in result or "{{INLINE_MATH_" in result:
        logger.warning("[merger] 回填后仍存在残留占位符！请检查 formula_dict/inline_dict。")
    else:
        logger.info("[merger] 回填完成，无残留占位符")

    return {"final_latex": result}
