"""
Node 5: 格式化 Agent（小模型）。
对占位符文本进行 LaTeX 排版，绝不触碰数学公式。
内置占位符完整性校验：如果小模型篡改了占位符，触发兜底机制。
"""
import re
from openai import OpenAI
from state.schema import AgentState
from config.settings import (
    SMALL_MODEL_API_KEY, SMALL_MODEL_BASE_URL, SMALL_MODEL_NAME,
    SMALL_MODEL_TEMPERATURE, SMALL_MODEL_MAX_TOKENS, SMALL_MODEL_TIMEOUT, logger,
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


def _wrap_fallback_latex(tagged_text: str) -> str:
    """
    将 tagged_text 包装为最小可编译的 LaTeX 文档。
    当小模型排版多次失败时作为兜底方案。
    """
    text = tagged_text
    # 清理段落标记
    text = re.sub(r'【题干】[：:]?\s*', '', text)
    text = re.sub(r'【小问设置】[：:]?\s*', '', text)
    text = re.sub(r'【详细解答】[：:]?\s*|【解答】[：:]?\s*',
                  r'\\textbf{参考答案}\n\n', text)
    # 确保 BLOCK_MATH 占位符独占一行
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

    client = OpenAI(
        api_key=SMALL_MODEL_API_KEY,
        base_url=SMALL_MODEL_BASE_URL,
        timeout=SMALL_MODEL_TIMEOUT,
        max_retries=3,
    )

    messages = [
        {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
        {"role": "user", "content": FORMATTER_USER_PROMPT.format(
            tagged_text=state["tagged_text"],
        )},
    ]

    import time
    total_p_tok = 0
    total_c_tok = 0
    total_t_tok = 0
    total_elapsed = 0.0
    MAX_ATTEMPTS = 2

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(f"[formatter] 正在等待小模型排版（第 {attempt}/{MAX_ATTEMPTS} 次）...")
            t0 = time.time()
            response = client.chat.completions.create(
                model=SMALL_MODEL_NAME,
                messages=messages,
                temperature=SMALL_MODEL_TEMPERATURE,
                max_tokens=SMALL_MODEL_MAX_TOKENS,
                stream=True,
                stream_options={"include_usage": True},
            )
            formatted = ""
            usage = None
            for chunk in response:
                if chunk.usage:
                    usage = chunk.usage
                if chunk.choices and chunk.choices[0].delta.content:
                    formatted += chunk.choices[0].delta.content
            elapsed = time.time() - t0
            p_tok = usage.prompt_tokens if usage else 0
            c_tok = usage.completion_tokens if usage else 0
            t_tok = usage.total_tokens if usage else 0
            total_p_tok += p_tok
            total_c_tok += c_tok
            total_t_tok += t_tok
            total_elapsed += elapsed
            logger.info(
                f"[formatter] 第 {attempt} 次排版完成 | {len(formatted)} 字符 | "
                f"{elapsed:.0f}s | tokens: {p_tok}+{c_tok}={t_tok}"
            )

            # 清理 markdown 代码围栏
            formatted = _strip_code_fences(formatted)
            # 清理占位符外的多余花括号和数学环境包裹
            formatted = _clean_placeholder_braces(formatted)

            if _validate_placeholders(state["tagged_text"], formatted):
                logger.info("[formatter] 占位符校验通过")
                from utils.run_stats import record
                record("formatter", len(formatted), total_elapsed,
                       prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
                       total_tokens=total_t_tok)
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

    # 所有尝试失败 → 用 tagged_text 包装为基本 LaTeX 文档兜底
    logger.warning(
        "[formatter] 所有尝试均失败，使用 tagged_text 包装为基本 LaTeX 文档兜底"
    )
    from utils.run_stats import record
    record("formatter", 0, total_elapsed,
           prompt_tokens=total_p_tok, completion_tokens=total_c_tok,
           total_tokens=total_t_tok)
    fallback = _wrap_fallback_latex(state["tagged_text"])
    return {"formatted_text": fallback}
