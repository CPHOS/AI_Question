"""
Node 4: 正则隔离器（纯 Python，不调用任何 LLM）。
双重隔离：先提取 Block 公式，再提取 Inline 公式，防止嵌套污染。
使用 reversed() 从后向前替换，确保前面的 match.start()/end() 不会因替换而偏移。
"""
import re
from state.schema import AgentState
from config.settings import (
    BLOCK_MATH_PATTERN, BLOCK_PLACEHOLDER_PREFIX, BLOCK_PLACEHOLDER_SUFFIX,
    INLINE_MATH_PATTERN, INLINE_PLACEHOLDER_PREFIX, INLINE_PLACEHOLDER_SUFFIX,
    logger,
)


def _sanitize_block_tags(text: str) -> str:
    r"""
    预处理：修正 LLM 常见的标签格式错误。
    - \end{block_math} → </block_math>
    - \begin{block_math} → <block_math>
    """
    # 修正 \end{block_math} → </block_math>
    text = re.sub(r'\\end\{block_math\}', '</block_math>', text)
    # 修正 \begin{block_math label="..."} 这种混合写法（极少见但防御性处理）
    text = re.sub(r'\\begin\{block_math\s+label="([^"]+)"\}', r'<block_math label="\1">', text)
    return text


def python_parser(state: AgentState) -> dict:
    """
    正则隔离器：
    Phase 0: 预处理修正 LLM 常见的标签格式错误
    Phase 1: 提取 <block_math> 标签 → 替换为 {{BLOCK_MATH_N}}
    Phase 2: 在已净化的文本中提取 $...$ → 替换为 {{INLINE_MATH_N}}

    替换均使用 reversed() 从后向前进行，
    这样前面匹配项的 start/end 索引不会因已替换内容的长度变化而失效。
    """
    logger.info("[parser] 进入正则隔离器")
    text = state["draft_content"]

    # ===== Phase 0: 预处理修正错误标签 =====
    text = _sanitize_block_tags(text)

    formula_dict: dict[str, dict[str, str]] = {}
    inline_dict: dict[str, str] = {}

    # ===== Phase 1: 提取 Block 公式 =====
    block_matches = list(re.finditer(BLOCK_MATH_PATTERN, text, re.DOTALL))
    for idx, match in enumerate(reversed(block_matches), start=1):
        label = match.group(1).strip()
        content = match.group(2).strip()
        placeholder = f"{BLOCK_PLACEHOLDER_PREFIX}{idx}{BLOCK_PLACEHOLDER_SUFFIX}"
        formula_dict[placeholder] = {"label": label, "content": content}
        # 替换时前后各加换行，确保占位符独占一行
        text = text[:match.start()] + f"\n{placeholder}\n" + text[match.end():]

    # ===== Phase 2: 提取 Inline 公式（在 Block 已被移除的文本上操作） =====
    inline_matches = list(re.finditer(INLINE_MATH_PATTERN, text))
    for idx, match in enumerate(reversed(inline_matches), start=1):
        content = match.group(1).strip()
        placeholder = f"{INLINE_PLACEHOLDER_PREFIX}{idx}{INLINE_PLACEHOLDER_SUFFIX}"
        inline_dict[placeholder] = content
        text = text[:match.start()] + placeholder + text[match.end():]

    logger.info(
        f"[parser] 提取完成 | Block 公式: {len(formula_dict)} 个 | "
        f"Inline 公式: {len(inline_dict)} 个"
    )

    return {
        "formula_dict": formula_dict,
        "inline_dict": inline_dict,
        "tagged_text": text,
    }
