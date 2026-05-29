"""model.stats 的 contextvars 并发隔离测试。"""
import threading

from model import stats


def test_run_context_isolates_records():
    stats.clear()
    with stats.run_context():
        stats.record("a", 1, 1.0, total_tokens=5)
        assert "a" in stats.get_all()
    # 退出上下文后，默认上下文不受影响
    assert "a" not in stats.get_all()


def test_nested_run_context_restores_previous():
    with stats.run_context():
        stats.record("outer", 1, 1.0)
        with stats.run_context():
            stats.record("inner", 1, 1.0)
            assert set(stats.get_all()) == {"inner"}
        assert set(stats.get_all()) == {"outer"}


def test_get_total_tokens_sums_current_context():
    with stats.run_context():
        stats.record("n1", 1, 1.0, prompt_tokens=2, completion_tokens=3, total_tokens=5)
        stats.record("n2", 1, 1.0, prompt_tokens=1, completion_tokens=1, total_tokens=2)
        tok = stats.get_total_tokens()
        assert tok == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}


def test_concurrent_threads_do_not_leak():
    """两个线程各自绑定上下文写入，互不串扰。"""
    results: dict[str, set] = {}
    barrier = threading.Barrier(2)

    def worker(name: str, node: str):
        with stats.run_context():
            barrier.wait()  # 确保两个线程上下文并存
            stats.record(node, 1, 1.0)
            barrier.wait()
            results[name] = set(stats.get_all())

    t1 = threading.Thread(target=worker, args=("t1", "node1"))
    t2 = threading.Thread(target=worker, args=("t2", "node2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["t1"] == {"node1"}
    assert results["t2"] == {"node2"}


def test_copy_context_propagates_into_threadpool():
    """模拟 reviewers：copy_context().run 使工作线程继承当前统计上下文。"""
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    with stats.run_context():
        def _child():
            stats.record("child", 1, 1.0)

        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(copy_context().run, _child).result()

        assert "child" in stats.get_all()
