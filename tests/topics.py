"""
测试用主题池加载器：从 tests/fixtures/topics.js 中解析 TOPIC_POOL 数组。
仅供测试使用，不在生产代码中引用。
"""
import re
import random
from pathlib import Path


_TOPICS_JS_PATH = Path(__file__).resolve().parent / "fixtures" / "topics.js"


def _parse_topics_js(filepath: Path) -> list[str]:
    """从 JavaScript 文件中提取 TOPIC_POOL 字符串数组。"""
    if not filepath.exists():
        raise FileNotFoundError(f"主题文件未找到: {filepath}")

    text = filepath.read_text(encoding="utf-8")
    topics = re.findall(r'"([^"]+)"', text)

    if not topics:
        raise ValueError(f"未能从 {filepath} 中解析到任何主题")

    return topics


TOPIC_POOL: list[str] = _parse_topics_js(_TOPICS_JS_PATH)


def get_random_topic() -> str:
    """从主题池中随机选取一个主题。"""
    return random.choice(TOPIC_POOL)
