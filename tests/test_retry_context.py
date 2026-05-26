from utils.retry_context import (
    build_problem_retry_context,
    build_solution_retry_context,
)


def test_build_problem_retry_context_separates_task_feedback_and_previous_problem():
    context = build_problem_retry_context(
        arbiter_feedback="题干中边界条件矛盾",
        problem_text="旧题干",
        planning_notes="保留三问结构",
    )

    assert "【本轮任务】" in context
    assert "【必须处理的仲裁反馈】\n题干中边界条件矛盾" in context
    assert "【应保留的命题规划约束】\n保留三问结构" in context
    assert "【上一版题干】\n旧题干" in context
    assert "不得沿用旧解答" in context


def test_build_solution_retry_context_marks_problem_as_immutable():
    context = build_solution_retry_context(
        arbiter_feedback="第二问积分上下限错误",
        problem_text="题干",
        solution_text="旧解答",
    )

    assert "【本轮任务】" in context
    assert "【必须处理的仲裁反馈】\n第二问积分上下限错误" in context
    assert "【不可修改的题干】\n题干" in context
    assert "【上一版解答】\n旧解答" in context
    assert "保留题干和小问不变" in context
