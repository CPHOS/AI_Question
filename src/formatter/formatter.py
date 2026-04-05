"""
格式化 Agent（小模型）。
对占位符文本进行 LaTeX 排版，绝不触碰数学公式。
内置占位符完整性校验：如果小模型篡改了占位符，触发兜底机制。
"""
import re
import time

from model.state import AgentState
from model.stats import record
from client import get_client, stream_chat
from config.settings import (
    SMALL_MODEL_NAME, SMALL_MODEL_TEMPERATURE, SMALL_MODEL_MAX_TOKENS, logger,
)
from prompts import load


def _strip_code_fences(text: str) -> str:
    """移除 LLM 常见的 markdown 代码围栏。"""
    text = text.strip()
    text = re.sub(r'^```\w*\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text


def _clean_placeholder_braces(text: str) -> str:
    """清理小模型在占位符外额外添加的花括号。"""
    text = re.sub(r'\{+(\{BLOCK_MATH_\d+\})\}+', r'{\1}', text)
    text = re.sub(r'\{+(\{INLINE_MATH_\d+\})\}+', r'{\1}', text)
    text = re.sub(r'\\\[\s*(\{\{BLOCK_MATH_\d+\}\})\s*\\\]', r'\1', text)
    text = re.sub(r'\$\s*(\{\{INLINE_MATH_\d+\}\})\s*\$', r'\1', text)
    return text


def _validate_placeholders(original: str, formatted: str) -> bool:
    """校验排版后的文本是否保留了所有占位符（数量和内容完全一致）。"""
    block_pat = re.compile(r'\{\{BLOCK_MATH_\d+\}\}')
    inline_pat = re.compile(r'\{\{INLINE_MATH_\d+\}\}')

    orig_blocks = sorted(block_pat.findall(original))
    fmt_blocks = sorted(block_pat.findall(formatted))
    orig_inlines = sorted(inline_pat.findall(original))
    fmt_inlines = sorted(inline_pat.findall(formatted))

    return (orig_blocks == fmt_blocks) and (orig_inlines == fmt_inlines)


def _wrap_fallback_latex(tagged_text: str) -> str:
    """
    将 tagged_text 包装为最小可编译的 LaTeX 文档。
    当小模型排版多次失败时作为兜底方案。
    """
    text = tagged_text
    text = re.sub(r'【题干】[：:]?\s*', '', text)
    text = re.sub(r'【小问设置】[：:]?\s*', '', text)
    text = re.sub(r'【详细解答】[：:]?\s*|【解答】[：:]?\s*',
                  r'\\textbf{参考答案}\n\n', text)
    text = re.sub(r'(?<!\n)\n?(\{\{BLOCK_MATH_\d+\}\})\n?(?!\n)',
                  r'\n\n\1\n\n', text)
    return (
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage{ctex}\n"
        "\\usepackage{amsmath,amssymb,bm}\n"
        "\\usepackage{geometry}\n"
        "\\pagestyle{empty}\n\n"
        "\\begin{document}\n\n"
        f"{text}\n\n"
        "\\end{document}\n"
    )


def formatting_agent(state: AgentState) -> dict:
    """
    格式化节点：
    1. 将 tagged_text 发送给小模型排版
    2. 校验占位符完整性
    3. 校验失败重试一次（附带纠正提示）
    4. 仍然失败则用 tagged_text 包装为基本 LaTeX 文档兜底
    """
    logger.info("[formatter] 进入格式化节点（小模型排版）")

    client = get_client()

    messages = [
        {"role": "system", "content": load("formatter", "system_prompt")},
        {"role": "user", "content": load("formatter", "user_prompt",
            tagged_text=state["tagged_text"])},
    ]

    total_p_tok = 0
    total_c_tok = 0
    total_t_tok = 0
    total_elapsed = 0.0
    MAX_ATTEMPTS = 2

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(f"[formatter] 正在等待小模型排版（第 {attempt}/{MAX_ATTEMPTS} 次）...")
            t0 = time.time()
            formatted, usage = stream_chat(
                client,
                model=SMALL_MODEL_NAME,
                messages=messages,
                temperature=SMALL_MODEL_TEMPERATURE,
                max_tokens=SMALL_MODEL_MAX_TOKENS,
            )
            elapsed = time.time() - t0
            total_p_tok += usage.prompt_tokens
            total_c_tok += usage.completion_tokens
            total_t_tok += usage.total_tokens
            total_elapsed += elapsed
            logger.info(
                f"[formatter] 第 {attempt} 次排版完成 | {len(formatted)} 字符 | "
                f"{elapsed:.0f}s | tokens: {usage.prompt_tokens}+{usage.completion_tokens}={usage.total_tokens}"
            )

            formatted = _strip_code_fences(formatted)
            formatted = _clean_placeholder_braces(formatted)

            if _validate_placeholders(state["tagged_text"], formatted):
                logger.info("[formatter] 占位符校验通过")
                record(
                    "formatter", len(formatted), total_elapsed,
                    prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
                    total_tokens=total_t_tok,
                )
                return {"formatted_text": formatted}
            else:
                logger.warning(f"[formatter] 第 {attempt} 次排版占位符校验失败")
                if attempt < MAX_ATTEMPTS:
                    messages.append({"role": "assistant", "content": formatted})
                    messages.append({"role": "user", "content": (
                        "你的输出中有占位符被修改或遗漏，这是不允许的。"
                        "请重新排版，确保所有 {{BLOCK_MATH_X}} 和 "
                        "{{INLINE_MATH_X}} 占位符原样保留，不要增删任何一个。"
                    )})

        except Exception as e:
            logger.error(f"[formatter] 第 {attempt} 次小模型请求失败: {e}")
            if attempt < MAX_ATTEMPTS:
                continue

    logger.warning(
        "[formatter] 所有尝试均失败，使用 tagged_text 包装为基本 LaTeX 文档兜底"
    )
    record(
        "formatter", 0, total_elapsed,
        prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
        total_tokens=total_t_tok,
    )
    fallback = _wrap_fallback_latex(state["tagged_text"])
    return {"formatted_text": fallback}
