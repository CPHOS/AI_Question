"""源材料加载器。

当前 workflow 仍然只接收一段 `source_material` 文本。这里把不同文件
类型归一为可进入 prompt 的文本，并在入口处控制长度，避免 PDF 全文
反复进入命题规划和命题生成阶段。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config.config import SOURCE_MATERIAL_MAX_CHARS


_TEXT_EXTENSIONS = {".txt", ".md", ".tex", ".csv", ".tsv", ".json"}


def _truncate(text: str, *, max_chars: int = SOURCE_MATERIAL_MAX_CHARS) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return (
        text[:max_chars].rstrip()
        + f"\n\n【源材料截断】以上保留前 {max_chars} 字符，另有 {omitted} 字符未进入本次上下文。"
    )


def _load_pdf(path: Path) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError(
            "无法读取 PDF 源材料：当前环境未找到 pdftotext。"
            "请安装 poppler 或 TeX Live 自带的 pdftotext。"
        )

    proc = subprocess.run(
        [executable, "-layout", str(path), "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    raw = proc.stdout.decode("utf-8", errors="replace")
    pages = [page.strip() for page in raw.split("\f") if page.strip()]
    if not pages:
        return f"【源文件】{path.name}\n【PDF 抽取结果】空"
    sections = [f"【PDF 第 {idx} 页】\n{page}" for idx, page in enumerate(pages, start=1)]
    return f"【源文件】{path.name}\n" + "\n\n".join(sections)


def load_source_material(filepath: str) -> str:
    """读取源材料文件并返回 prompt 可用文本。"""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"源材料文件未找到: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"源材料路径不是文件: {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _load_pdf(path)
    elif suffix in _TEXT_EXTENSIONS:
        text = f"【源文件】{path.name}\n" + path.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"不支持的源材料文件类型: {suffix or '无扩展名'}。"
            "当前支持 .pdf, .txt, .md, .tex, .csv, .tsv, .json。"
        )

    return _truncate(text)
