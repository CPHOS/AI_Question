"""
运行时统计收集器 — 各节点写入耗时/字符数/token数，运行结束时读取并汇总。

并发隔离
========
本模块使用 :class:`contextvars.ContextVar` 持有每个运行任务独立的统计字典，
而非进程级全局单例。这样在 API 后端并发执行多个生成任务时，各任务的统计
互不串扰；CLI 单任务场景同样适用（默认上下文）。

公开 API（``record`` / ``get_all`` / ``get_total_tokens`` / ``clear``）保持与
历史版本一致的签名，各 Agent 无需改动调用方式。新增 :func:`run_context`
上下文管理器用于在任务边界绑定一份独立的统计字典。

线程传播
========
:class:`concurrent.futures.ThreadPoolExecutor` 默认不会把调用线程的
``ContextVar`` 复制到工作线程。需要在工作线程内写入统计的代码（如并行
审核）应使用 ``contextvars.copy_context().run(fn, *args)`` 提交任务，以便
工作线程继承当前任务的统计上下文。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

# 每个任务独立的统计字典；默认值为进程级共享字典，兼容 CLI 单任务场景。
_stats_var: contextvars.ContextVar[dict[str, dict]] = contextvars.ContextVar(
    "physics_generator_stats",
    default={},
)


def _current() -> dict[str, dict]:
    """返回当前上下文的统计字典。"""
    return _stats_var.get()


def record(node: str, chars: int, elapsed: float, extra: str = "",
           prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
    """记录一个节点的输出。

    Args:
        node: 节点标识（如 ``planner`` / ``problem_gen_r1``）。
        chars: 该节点输出字符数。
        elapsed: 该节点耗时（秒）。
        extra: 附加说明文本。
        prompt_tokens: 提示 token 数。
        completion_tokens: 补全 token 数。
        total_tokens: 总 token 数。
    """
    _current()[node] = {
        "chars": chars,
        "elapsed": elapsed,
        "extra": extra,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def get_all() -> dict[str, dict]:
    """返回当前上下文所有节点统计的浅拷贝。"""
    return dict(_current())


def get_total_tokens() -> dict[str, int]:
    """汇总当前上下文所有节点的 token 用量。"""
    stats = _current()
    prompt = sum(s.get("prompt_tokens", 0) for s in stats.values())
    completion = sum(s.get("completion_tokens", 0) for s in stats.values())
    total = sum(s.get("total_tokens", 0) for s in stats.values())
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def clear() -> None:
    """清空当前上下文的统计数据。"""
    _current().clear()


@contextmanager
def run_context() -> Iterator[dict[str, dict]]:
    """绑定一份独立的统计字典，供单个生成任务使用。

    进入时把一个新的空字典设置到 :class:`~contextvars.ContextVar`，退出时
    恢复先前的上下文。并发任务各自 ``with run_context():`` 即可隔离统计。

    Yields:
        本次任务专属的统计字典（也可直接通过 :func:`get_all` 读取）。
    """
    fresh: dict[str, dict] = {}
    token = _stats_var.set(fresh)
    try:
        yield fresh
    finally:
        _stats_var.reset(token)
