"""
回填器单元测试。
运行: uv run pytest tests/test_merger.py -v
"""
from latex.merge import merge as python_merger


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
        assert "\\begin{figure}[H]" in result["final_latex"]
        assert "fig/fig1.pdf" in result["final_latex"]
        assert "\\caption{系统示意图}" in result["final_latex"]
        assert "\\label{fig:1}" in result["final_latex"]
        assert "{{FIGURE_" not in result["final_latex"]
        assert result["figure_descriptions"]["fig_1"]["filename"] == "fig1.pdf"
        assert result["figure_descriptions"]["fig_1"]["tikz_filename"] == "fig1.tex"

    def test_figure_caption_escapes_latex_special_chars(self):
        state = _make_state(
            formatted_text="{{FIGURE_1}}",
            figure_dict={
                "{{FIGURE_1}}": {
                    "label": "fig:test",
                    "caption": "$v^2$~a_b & 50%",
                    "description": "figure",
                },
            },
        )
        result = python_merger(state)
        assert "\\caption{\\$v\\textasciicircum{}2\\$\\textasciitilde{}a\\_b \\& 50\\%}" in result["final_latex"]
        assert result["figure_descriptions"]["fig_1"]["caption"] == "$v^2$~a_b & 50%"

    def test_markdown_and_standalone_inline_cleanup(self):
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "**(1)[5分]**\n"
                "{{INLINE_MATH_1}}\n"
                "\\end{solution}\n"
            ),
            inline_dict={"{{INLINE_MATH_1}}": "a=b+c"},
        )
        result = python_merger(state)
        assert "**" not in result["final_latex"]
        assert "\\[\na=b+c\n\\]" in result["final_latex"]

    def test_markdown_cleanup_preserves_literal_double_underscore(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "变量 a__b 保持原样，__强调__ 去掉标记。\n"
                "\\end{problemstatement}\n"
            ),
        )
        result = python_merger(state)
        assert "a__b" in result["final_latex"]
        assert "强调" in result["final_latex"]
        assert "__强调__" not in result["final_latex"]


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

    def test_problemstatement_fullwidth_subq(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "题干内容\n\n"
                "（1）第一问\n\n"
                "（1.1）推导子问\n"
                "\\end{problemstatement}\n"
            ),
        )
        result = python_merger(state)
        assert "\\subq{1}\\label{q:1} 第一问" in result["final_latex"]
        assert "\\subsubq{1.1}\\label{q:1.1} 推导子问" in result["final_latex"]

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

    def test_part_labels_prefix_repeated_subquestions(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "**A. 建模 (8分)**\n"
                "(1) 第一问\n"
                "**B. 推导 (12分)**\n"
                "(1) 第一问\n"
                "\\end{problemstatement}"
            ),
        )
        result = python_merger(state)
        assert "\\pmark{A}\\label{part:A} 建模 (8分)" in result["final_latex"]
        assert "\\pmark{B}\\label{part:B} 推导 (12分)" in result["final_latex"]
        assert "\\subq{1}\\label{q:A.1}" in result["final_latex"]
        assert "\\subq{1}\\label{q:B.1}" in result["final_latex"]

    def test_unscored_markdown_part_heading(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "**A. 建模**\n"
                "(1) 第一问\n"
                "\\end{problemstatement}"
            ),
        )
        result = python_merger(state)["final_latex"]
        assert "\\pmark{A}\\label{part:A} 建模" in result
        assert "\\subq{1}\\label{q:A.1}" in result

    def test_action_list_is_not_part_heading(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "(1) 设计四步动作：\n"
                "A. 锁定长度，改变角度；\n"
                "B. 改变长度，锁定角度。\n"
                "(2) 分析净位移。\n"
                "\\end{problemstatement}"
            ),
        )
        result = python_merger(state)["final_latex"]
        assert "\\pmark{A}" not in result
        assert "A. 锁定长度，改变角度；" in result
        assert "\\subq{2}\\label{q:2}" in result

    def test_question_commands_start_new_paragraphs(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problemstatement}\n"
                "引言。\n"
                "(1) 第一问\n"
                "(1.1) 子问\n"
                "\\end{problemstatement}"
            ),
        )
        result = python_merger(state)["final_latex"]
        assert "引言。\n\n\\subq{1}\\label{q:1} 第一问" in result
        assert "第一问\n\n\\subsubq{1.1}\\label{q:1.1} 子问\n\\end{problemstatement}" in result

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

    def test_solution_fullwidth_subq(self):
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "（1）[15分]\n"
                "解答\n\n"
                "（1.1）[5分]\n"
                "子问解答\n"
                "\\end{solution}\n"
            ),
        )
        result = python_merger(state)
        assert "\\solsubq{1}{15}" in result["final_latex"]
        assert "\\solsubsubq{1.1}{5}" in result["final_latex"]

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

    def test_solution_markdown_part_headings(self):
        state = _make_state(
            formatted_text=(
                "\\begin{solution}\n"
                "**A. 建模 (8分)**\n"
                "(1)[4分]\n"
                "解答\n"
                "\\end{solution}\n"
            ),
        )
        result = python_merger(state)
        assert "\\solPart{A}{8}" in result["final_latex"]
        assert "\\solsubq{1}{4}" in result["final_latex"]


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

    def test_problem_title_escapes_underscore(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{Physics_Model_Big_Wheel_Pendulum}\n"
                "\\begin{solution}\n"
                "(1)[40分]\n解答\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)
        assert "\\begin{problem}[40]{Physics\\_Model\\_Big\\_Wheel\\_Pendulum}" in result["final_latex"]

    def test_problem_title_does_not_double_escape_underscore(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{Physics\\_Model\\_Big\\_Wheel\\_Pendulum}\n"
                "\\begin{solution}\n"
                "(1)[40分]\n解答\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)
        assert "\\begin{problem}[40]{Physics\\_Model\\_Big\\_Wheel\\_Pendulum}" in result["final_latex"]
        assert "\\\\_Model" not in result["final_latex"]

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

    def test_total_score_mixed_parent_and_leaf_groups(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{test}\n"
                "\\begin{solution}\n"
                "(1)[10分]\n"
                "(1.1)[4分]\n"
                "(1.2)[6分]\n"
                "(2.1)[15分]\n"
                "(2.2)[15分]\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)
        assert "\\begin{problem}[40]{test}" in result["final_latex"]

    def test_missing_solution_parent_headers_are_inserted(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{test}\n"
                "\\begin{problemstatement}\n"
                "(1) 一级\n"
                "(1.1) 子问甲\n"
                "(1.2) 子问乙\n"
                "\\end{problemstatement}\n"
                "\\begin{solution}\n"
                "(1.1)[4分]\n"
                "解答甲\n"
                "(1.2)[6分]\n"
                "解答乙\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)["final_latex"]
        assert "\\solsubq{1}{10}\n\n\\solsubsubq{1.1}{4}" in result
        assert "\\begin{problem}[10]{test}" in result

    def test_missing_solution_parent_headers_are_inserted_recursively(self):
        state = _make_state(
            formatted_text=(
                "\\begin{problem}{test}\n"
                "\\begin{problemstatement}\n"
                "(1) 一级\n"
                "(1.1) 二级\n"
                "(1.1.1) 三级甲\n"
                "(1.1.2) 三级乙\n"
                "\\end{problemstatement}\n"
                "\\begin{solution}\n"
                "(1.1.1)[4分]\n"
                "解答甲\n"
                "(1.1.2)[6分]\n"
                "解答乙\n"
                "\\end{solution}\n"
                "\\end{problem}\n"
            ),
        )
        result = python_merger(state)["final_latex"]
        assert "\\solsubq{1}{10}\n\n\\solsubsubq{1.1}{10}\n\n\\solsubsubsubq{1.1.1}{4}" in result
        assert "\\begin{problem}[10]{test}" in result
