"""标题解析工具。"""
import re


_TITLE_PATTERNS = (
    r'^\s*【标题】\s*[：:]\s*(?P<title>[^\n]+)\s*\n?',
    r'^\s*【标题】\s*(?P<title>[^\n]+)\s*\n?',
    r'^\s*标题\s*[：:]\s*(?P<title>[^\n]+)\s*\n?',
    r'^\s*#\s+(?P<title>[^\n]+)\s*\n?',
)


def is_noisy_title(title: str) -> bool:
    """判断标题是否仍然像输入文件名或导出页名称。"""
    clean = title.strip()
    if not clean:
        return True
    return (
        clean.startswith("导出页面自")
        or clean.lower().endswith((".pdf", ".tex", ".md"))
        or "_" in clean
    )


def infer_title_from_content(text: str, fallback: str = "") -> str:
    """在模型漏写标题时，从题干关键词生成一个展示用短标题。"""
    compact = re.sub(r"\s+", " ", text)
    if ("Johnson" in compact or "Nyquist" in compact or "热噪声" in compact) and (
        "传输线" in compact or "黑体" in compact or "LC" in compact
    ):
        return "一维黑体辐射与 Johnson 噪声"
    if ("变径" in compact or "Big Wheel" in compact) and "圆环" in compact:
        return "变径摆驱动圆环"
    if ("LC" in compact or "阶梯网络" in compact) and "传输线" in compact:
        return "LC 传输线色散"

    clean = fallback.strip()
    clean = re.sub(r"^导出页面自\s*", "", clean)
    clean = re.sub(r"\.(pdf|tex|md)$", "", clean, flags=re.IGNORECASE)
    clean = clean.replace("_", " ").strip()
    return clean


def extract_leading_title(text: str, default: str = "") -> tuple[str, str]:
    """从文本开头提取标题，并返回标题与剩余正文。"""
    for pattern in _TITLE_PATTERNS:
        match = re.match(pattern, text)
        if not match:
            continue
        title = match.group("title").strip()
        if title:
            return title, text[match.end():]
    return default, text
