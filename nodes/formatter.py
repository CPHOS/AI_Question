"""
Node 5: 格式化 Agent（小模型）。
对占位符文本进行 LaTeX 排版，绝不触碰数学公式。
内置占位符完整性校验：如果小模型篡改了占位符，触发兜底机制。
"""
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state.schema import AgentState
from config.settings import (
    SMALL_MODEL_API_KEY, SMALL_MODEL_BASE_URL, SMALL_MODEL_NAME,
    SMALL_MODEL_TEMPERATURE, SMALL_MODEL_MAX_TOKENS, logger,
)
from config.prompts import FORMATTER_SYSTEM_PROMPT, FORMATTER_USER_PROMPT


def _strip_code_fences(text: str) -> str:
    """
    移除 LLM 常见的 markdown 代码围栏。
    如 ```latex\n...\n``` 或 ```\n...\n```
    """
    text = text.strip()
    # 移除开头的 ```xxx
    text = re.sub(r'^```\w*\s*\n?', '', text)
    # 移除结尾的 ```
    text = re.sub(r'\n?```\s*$', '', text)
    return text


def _clean_placeholder_braces(text: str) -> str:
    """
    清理小模型在占位符外额外添加的花括号。
    如 {{{BLOCK_MATH_1}}} → {{BLOCK_MATH_1}}
    如 {{{{{INLINE_MATH_1}}}}} → {{INLINE_MATH_1}}
    同时清理 \\[ \\] 和 $ 等数学环境包裹占位符的情况。
    """
    # 清理 Block 占位符外的多余花括号: {+{{BLOCK_MATH_N}}+} → {{BLOCK_MATH_N}}
    text = re.sub(r'\{+(\{BLOCK_MATH_\d+\})\}+', r'{\1}', text)
    text = re.sub(r'\{+(\{INLINE_MATH_\d+\})\}+', r'{\1}', text)
    # 清理 \[ {{BLOCK_MATH_N}} \] → {{BLOCK_MATH_N}}
    text = re.sub(r'\\\[\s*(\{\{BLOCK_MATH_\d+\}\})\s*\\\]', r'\1', text)
    # 清理 $ {{INLINE_MATH_N}} $ → {{INLINE_MATH_N}}
    text = re.sub(r'\$\s*(\{\{INLINE_MATH_\d+\}\})\s*\$', r'\1', text)
    return text


def _validate_placeholders(original: str, formatted: str) -> bool:
    """
    校验排版后的文本是否保留了所有占位符（数量和内容完全一致）。
    分别校验 Block 和 Inline 两类占位符。
    """
    block_pat = re.compile(r'\{\{BLOCK_MATH_\d+\}\}')
    inline_pat = re.compile(r'\{\{INLINE_MATH_\d+\}\}')

    orig_blocks = sorted(block_pat.findall(original))
    fmt_blocks = sorted(block_pat.findall(formatted))
    orig_inlines = sorted(inline_pat.findall(original))
    fmt_inlines = sorted(inline_pat.findall(formatted))

    return (orig_blocks == fmt_blocks) and (orig_inlines == fmt_inlines)


def formatting_agent(state: AgentState) -> dict:
    """
    格式化节点：
    1. 将 tagged_text 发送给小模型排版
    2. 校验占位符完整性
    3. 校验失败则使用原始 tagged_text 兜底
    """
    logger.info("[formatter] 进入格式化节点（小模型排版）")

    llm = ChatOpenAI(
        api_key=SMALL_MODEL_API_KEY,
        base_url=SMALL_MODEL_BASE_URL,
        model=SMALL_MODEL_NAME,
        temperature=SMALL_MODEL_TEMPERATURE,
        max_tokens=SMALL_MODEL_MAX_TOKENS,
        max_retries=3,
    )

    messages = [
        SystemMessage(content=FORMATTER_SYSTEM_PROMPT),
        HumanMessage(content=FORMATTER_USER_PROMPT.format(
            tagged_text=state["tagged_text"],
        )),
    ]

    try:
        response = llm.invoke(messages)
        formatted = response.content

        # 清理 markdown 代码围栏
        formatted = _strip_code_fences(formatted)
        # 清理占位符外的多余花括号和数学环境包裹
        formatted = _clean_placeholder_braces(formatted)

        if _validate_placeholders(state["tagged_text"], formatted):
            logger.info("[formatter] 占位符校验通过")
            return {"formatted_text": formatted}
        else:
            logger.warning(
                "[formatter] 小模型篡改了占位符！触发安全兜底，使用原始 tagged_text。"
            )
            return {"formatted_text": state["tagged_text"]}

    except Exception as e:
        logger.error(f"[formatter] 小模型请求失败: {e}，使用原始 tagged_text 兜底")
        return {"formatted_text": state["tagged_text"]}
