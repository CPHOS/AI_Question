"""
回填器单元测试。
运行: uv run pytest tests/test_merger.py -v
"""
from formatter.merger import python_merger


def _make_state(**overrides) -> dict:
    base = {
        "topic": "", "difficulty": "", "total_score": 50, "title": "",
        "draft_content": "",
        "math_review": "", "physics_review": "",
        "arbiter_decision": "", "arbiter_reason": "", "arbiter_feedback": "",
        "error_category": "",
        "retry_count": 0, "formula_dict": {}, "inline_dict": {},
        "figure_dict": {},
        "tagged_text": "", "formatted_text": "", "final_latex": "",
        "figure_descriptions": {},
    }
    base.update(overrides)
    return base


class TestBlockMerge:
    def test_single_block_merge(self):
        state = _make_state(
            formatted_text="文字\n{{BLOCK_MATH_1}}\n文字",
            formula_dict={
                "{{BLOCK_MATH_1}}": {"label": "eq:test", "content": "E = mc^2", "score": ""}
            },
        )
        result = python_merger(state)
        assert "\\begin{equation}" in result["final_latex"]
        assert "\\label{eq:1}" in result["final_latex"]
        assert "\\eqtag{1}" in result["final_latex"]
        assert "E = mc^2" in result["final_latex"]
        assert "{{BLOCK_MATH_" not in result["final_latex"]

    def test_scored_block_merge(self):
        state = _make_state(
            formatted_text="文字\n{{BLOCK_MATH_1}}\n文字",
            formula_dict={
                "{{BLOCK_MATH_1}}": {"label": "eq:f", "content": "F = ma", "score": "3"}
            },
        )
        result = python_merger(state)
        assert "\\eqtagscore{1}{3}" in result["final_latex"]
        assert "\\label{eq:1}" in result["final_latex"]


class TestInlineMerge:
    def test_single_inline_merge(self):
        state = _make_state(
            formatted_text="速度为 {{INLINE_MATH_1}} 米每秒",
            inline_dict={"{{INLINE_MATH_1}}": "v_0"},
        )
        result = python_merger(state)
        assert "$v_0$" in result["final_latex"]
        assert "{{INLINE_MATH_" not in result["final_latex"]


class TestMixedMerge:
    def test_block_and_inline(self):
        state = _make_state(
            formatted_text="由 {{INLINE_MATH_1}} 可知\n{{BLOCK_MATH_1}}\n结论",
            formula_dict={
                "{{BLOCK_MATH_1}}": {"label": "eq:a", "content": "a = b", "score": ""}
            },
            inline_dict={"{{INLINE_MATH_1}}": "F"},
        )
        result = python_merger(state)
        assert "$F$" in result["final_latex"]
        assert "\\begin{equation}" in result["final_latex"]
        assert "{{BLOCK_MATH_" not in result["final_latex"]
        assert "{{INLINE_MATH_" not in result["final_latex"]


class TestFigureMerge:
    def test_figure_backfill(self):
        state = _make_state(
            formatted_text="文字\n{{FIGURE_1}}\n文字",
            figure_dict={
                "{{FIGURE_1}}": {
                    "label": "fig:setup",
                    "caption": "系统示意图",
                    "description": "画一根导电细杆",
                },
            },
        )
        result = python_merger(state)
        assert "%\\begin{figure}[H]" in result["final_latex"]
        assert "fig/fig_1.pdf" in result["final_latex"]
        assert "%    \\caption{系统示意图}" in result["final_latex"]
        assert "%    \\label{fig:1}" in result["final_latex"]
        assert "{{FIGURE_" not in result["final_latex"]
        assert result["figure_descriptions"]["fig_1"]["filename"] == "fig_1.pdf"


class TestCrossRefRemap:
    def test_equation_ref_remap(self):
        state = _make_state(
            formatted_text="代入\\ref{eq:force}可得\n{{BLOCK_MATH_1}}\n结论",
            formula_dict={
                "{{BLOCK_MATH_1}}": {"label": "eq:force", "content": "F=ma", "score": ""}
            },
        )
        result = python_merger(state)
        assert "\\ref{eq:1}" in result["final_latex"]
        assert "\\ref{eq:force}" not in result["final_latex"]


class TestSubqConversion:
    def test_problemstatement_subq(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "题干内容\n\n"
                "(1) 第一问\n\n"
                "(2) 第二问\n"
                "\\end{problemstatement}\n"
            ),
        )
        result = python_merger(state)
        assert "\\subq{1}\\label{q:1}" in result["final_latex"]
        assert "\\subq{2}\\label{q:2}" in result["final_latex"]

    def test_problemstatement_subsubq(self):
        """二级小问 (1.1)(1.2) → \\subsubq"""
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "题干内容\n\n"
                "(1) 第一问\n\n"
                "(1.1) 子问题一\n\n"
                "(1.2) 子问题二\n"
                "\\end{problemstatement}\n"
            ),
        )
        result = python_merger(state)
        assert "\\subq{1}\\label{q:1}" in result["final_latex"]
        assert "\\subsubq{1.1}\\label{q:1.1}" in result["final_latex"]
        assert "\\subsubq{1.2}\\label{q:1.2}" in result["final_latex"]

    def test_problemstatement_subsubsubq(self):
        """三级小问 (1.1.1)(1.1.2) → \\subsubsubq"""
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "题干内容\n\n"
                "(1) 第一问\n\n"
                "(1.1) 子问题\n\n"
                "(1.1.1) 子子问题一\n\n"
                "(1.1.2) 子子问题二\n"
                "\\end{problemstatement}\n"
            ),
        )
        result = python_merger(state)
        assert "\\subsubsubq{1.1.1}\\label{q:1.1.1}" in result["final_latex"]
        assert "\\subsubsubq{1.1.2}\\label{q:1.1.2}" in result["final_latex"]

    def test_solution_solsubq(self):
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "(1)[15分]\n"
                "解答\n\n"
                "(2)[25分]\n"
                "解答\n"
                "\\end{solution}\n"
            ),
        )
        result = python_merger(state)
        assert "\\solsubq{1}{15}" in result["final_latex"]
        assert "\\solsubq{2}{25}" in result["final_latex"]

    def test_solution_solsubsubq(self):
        """二级解答 (1.1)[X分] → \\solsubsubq"""
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "(1)[30分]\n"
                "(1.1)[15分]\n"
                "解答\n\n"
                "(1.2)[15分]\n"
                "解答\n"
                "\\end{solution}\n"
            ),
        )
        result = python_merger(state)
        assert "\\solsubq{1}{30}" in result["final_latex"]
        assert "\\solsubsubq{1.1}{15}" in result["final_latex"]
        assert "\\solsubsubq{1.2}{15}" in result["final_latex"]

    def test_solution_solsubsubsubq(self):
        """三级解答 (1.1.1)[X分] → \\solsubsubsubq"""
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "(1)[20分]\n"
                "(1.1)[10分]\n"
                "解答\n\n"
                "(1.2)[10分]\n"
                "解答\n"
                "\\end{solution}\n"
            ),
        )
        result = python_merger(state)
        assert "\\solsubsubq{1.1}{10}" in result["final_latex"]
        assert "\\solsubsubq{1.2}{10}" in result["final_latex"]


class TestScoringAndTotal:
    def test_scoring_added(self):
        state = _make_state(
            formatted_text="\\begin{solution}\n解答\n\\end{solution}",
        )
        result = python_merger(state)
        assert "\\scoring" in result["final_latex"]

    def test_total_score_computed(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{}\n"
                "\\begin{solution}\n"
                "(1)[15分]\n解答\n"
                "(2)[25分]\n解答\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)
        assert "\\begin{problem}[40]" in result["final_latex"]

    def test_total_score_multilevel(self):
        """多层级时只累加一级小问分值"""
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{}\n"
                "\\begin{solution}\n"
                "(1)[20分]\n"
                "(1.1)[10分]\n解答\n"
                "(1.2)[10分]\n解答\n"
                "(2)[30分]\n解答\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)
        # 总分 = 20 + 30 = 50（不重复累加 1.1/1.2 的 10+10）
        assert "\\begin{problem}[50]" in result["final_latex"]
