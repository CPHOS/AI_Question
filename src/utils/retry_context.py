"""重试上下文构造工具。"""


def _clean_text(text: str) -> str:
    """统一清理重试上下文中的首尾空白。"""
    return text.strip() if text else "（空）"


def _section(title: str, body: str) -> str:
    return f"【{title}】\n{_clean_text(body)}"


def build_problem_retry_context(
    *,
    arbiter_feedback: str,
    problem_text: str,
    planning_notes: str,
) -> str:
    """构造命题重试上下文。"""
    sections = [
        _section(
            "本轮任务",
            (
                "重新生成完整题干和小问。上一版题干仅用于定位问题；"
                "不得沿用旧解答，参考答案会由解题 Agent 重新生成。"
            ),
        ),
        _section("必须处理的仲裁反馈", arbiter_feedback),
        _section("应保留的命题规划约束", planning_notes),
        _section("上一版题干", problem_text),
    ]
    return "\n\n".join(sections)


def build_solution_retry_context(
    *,
    arbiter_feedback: str,
    problem_text: str,
    solution_text: str,
) -> str:
    """构造解题重试上下文。"""
    sections = [
        _section(
            "本轮任务",
            (
                "保留题干和小问不变，重新生成完整参考答案和评分点。"
                "上一版解答仅用于定位问题。"
            ),
        ),
        _section("必须处理的仲裁反馈", arbiter_feedback),
        _section("不可修改的题干", problem_text),
        _section("上一版解答", solution_text),
    ]
    return "\n\n".join(sections)
