"""
文件写入辅助。
统一路径构造、JSON / Markdown / LaTeX 文件写入。
"""
import json
from pathlib import Path

from config.config import logger


def write_text(path: Path, content: str) -> Path:
    """写入文本文件，返回路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("[files] 写入: %s", path.name)
    return path


def write_json(path: Path, data: dict) -> Path:
    """写入 JSON 文件，返回路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[files] 写入: %s", path.name)
    return path
