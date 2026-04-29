"""
格式化 Agent（小模型）。
对占位符文本进行 LaTeX 排版，绝不触碰数学公式。
内置占位符完整性校验：如果小模型篡改了占位符，触发兜底机制。

数据归属（参见 model/state.py）：
  - 读取：LaTeXOutput.tagged_text
  - 写入：LaTeXOutput.formatted_text
"""
import re
import time

from model.state import WorkflowData, LaTeXOutput
from model.stats import record
from client import get_client, stream_chat
from config.config import (
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
    text = re.sub(r'\{+(\{FIGURE_\d+\})\}+', r'{\1}', text)
    text = re.sub(r'\\\[\s*(\{\{BLOCK_MATH_\d+\}\})\s*\\\]', r'\1', text)
    text = re.sub(r'\$\s*(\{\{INLINE_MATH_\d+\}\})\s*\$', r'\1', text)
    return text


def _validate_placeholders(original: str, formatted: str) -> bool:
    """校验排版后的文本是否保留了所有占位符。"""
    block_pat = re.compile(r'\{\{BLOCK_MATH_\d+\}\}')
    inline_pat = re.compile(r'\{\{INLINE_MATH_\d+\}\}')
    figure_pat = re.compile(r'\{\{FIGURE_\d+\}\}')

    return (
        sorted(block_pat.findall(original)) == sorted(block_pat.findall(formatted))
        and sorted(inline_pat.findall(original)) == sorted(inline_pat.findall(formatted))
        and sorted(figure_pat.findall(original)) == sorted(figure_pat.findall(formatted))
    )


def _wrap_fallback_latex(tagged_text: str, *, title: str = "") -> str:
    """兜底：将 tagged_text 包装为最小可编译的 CPHOS LaTeX 文档。"""
    text = tagged_text
    text = re.sub(r'【标题】[^\n]*\n?', '', text)
    text = re.sub(r'【题干】[：:]?\s*', '', text)
    text = re.sub(r'【小问设置】[：:]?\s*', '', text)

    sol_match = re.search(r'(?:参考答案|【详细解答】[：:]?\s*|【解答】[：:]?\s*)', text)
    if sol_match:
        stmt = text[:sol_match.start()].strip()
        sol = text[sol_match.end():].strip()
        score_match = re.search(r'\n\s*评分标准\s*\n', sol)
        if score_match:
            sol = sol[:score_match.start()].strip()
    else:
        stmt = text
        sol = ""

    stmt = re.sub(r'(?<!\n)\n?(\{\{BLOCK_MATH_\d+\}\})\n?(?!\n)', r'\n\n\1\n\n', stmt)

    result = (
        "\\documentclass[answer]{cphos}\n\n"
        "\\begin{document}\n"
        f"\\begin{{problem}}{{{title}}}\n\n"
        "\\begin{problemstatement}\n"
        f"{stmt}\n"
        "\\end{problemstatement}\n\n"
    )
    if sol:
        sol = re.sub(r'(?<!\n)\n?(\{\{BLOCK_MATH_\d+\}\})\n?(?!\n)', r'\n\n\1\n\n', sol)
        result += (
            "\\begin{solution}\n"
            f"{sol}\n"
            "\\end{solution}\n\n"
        )
    result += "\\end{problem}\n\n\\end{document}\n"
    return result


def formatting_agent(data: WorkflowData) -> LaTeXOutput:
    """
    格式化节点：
    1. 发送给小模型排版
    2. 校验占位符完整性
    3. 失败重试一次
    4. 仍失败则兜底
    """
    logger.info("[format] 进入格式化节点")

    client = get_client()
    messages = [
        {"role": "system", "content": load("formatter", "system_prompt")},
        {"role": "user", "content": load("formatter", "user_prompt",
            tagged_text=data["tagged_text"],
            title=data.get("title") or data.get("topic", ""))},
    ]

    total_p_tok = total_c_tok = total_t_tok = 0
    total_elapsed = 0.0
    MAX_ATTEMPTS = 2

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info("[format] 等待小模型排版 (第 %d/%d 次)...", attempt, MAX_ATTEMPTS)
            t0 = time.time()
            formatted, usage = stream_chat(
                client, model=SMALL_MODEL_NAME,
                messages=messages,
                temperature=SMALL_MODEL_TEMPERATURE,
                max_tokens=SMALL_MODEL_MAX_TOKENS,
            )
            elapsed = time.time() - t0
            total_p_tok += usage.prompt_tokens
            total_c_tok += usage.completion_tokens
            total_t_tok += usage.total_tokens
            total_elapsed += elapsed

            formatted = _strip_code_fences(formatted)
            formatted = _clean_placeholder_braces(formatted)

            if _validate_placeholders(data["tagged_text"], formatted):
                logger.info("[format] 占位符校验通过")
                record("formatter", len(formatted), total_elapsed,
                       prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
                       total_tokens=total_t_tok)
                return {"formatted_text": formatted}

            logger.warning("[format] 第 %d 次占位符校验失败", attempt)
            if attempt < MAX_ATTEMPTS:
                messages.append({"role": "assistant", "content": formatted})
                messages.append({"role": "user", "content": (
                    "你的输出中有占位符被修改或遗漏。"
                    "请重新排版，确保所有 {{BLOCK_MATH_X}}、{{INLINE_MATH_X}}、"
                    "{{FIGURE_X}} 占位符原样保留。"
                )})

        except Exception as e:
            logger.error("[format] 第 %d 次请求失败: %s", attempt, e)

    logger.warning("[format] 所有尝试失败，使用兜底 LaTeX 包装")
    record("formatter", 0, total_elapsed,
           prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
           total_tokens=total_t_tok)
    fallback = _wrap_fallback_latex(data["tagged_text"],
                                    title=data.get("title") or data.get("topic", ""))
    return {"formatted_text": fallback}
