"""
运行时统计收集器 — 各节点写入耗时/字符数/token数，main.py 结束时读取并写入 TEST_LOG.md。
"""

_stats: dict[str, dict] = {}


def record(node: str, chars: int, elapsed: float, extra: str = "",
           prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
    """记录一个节点的输出。"""
    _stats[node] = {
        "chars": chars,
        "elapsed": elapsed,
        "extra": extra,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def get_all() -> dict[str, dict]:
    return dict(_stats)


def get_total_tokens() -> dict[str, int]:
    """汇总所有节点的 token 用量。"""
    prompt = sum(s.get("prompt_tokens", 0) for s in _stats.values())
    completion = sum(s.get("completion_tokens", 0) for s in _stats.values())
    total = sum(s.get("total_tokens", 0) for s in _stats.values())
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def clear() -> None:
    _stats.clear()
