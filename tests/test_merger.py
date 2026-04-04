"""
回填器单元测试。
覆盖: Block 回填、Inline 回填、混合回填、空字典。
运行: python -m pytest tests/test_merger.py -v
"""
from nodes.merger import python_merger


def _make_state(**overrides) -> dict:
    base = {
        "topic": "", "difficulty": "", "draft_content": "",
        "math_review": "", "physics_review": "",
        "arbiter_decision": "", "arbiter_feedback": "",
        "retry_count": 0, "formula_dict": {}, "inline_dict": {},
        "tagged_text": "", "formatted_text": "", "final_latex": "",
    }
    base.update(overrides)
    return base


class TestBlockMerge:
    def test_single_block_merge(self):
        state = _make_state(
            formatted_text="文字\n{{BLOCK_MATH_1}}\n文字",
            formula_dict={
                "{{BLOCK_MATH_1}}": {"label": "eq:test", "content": "E = mc^2"}
            },
        )
        result = python_merger(state)
        assert "\\begin{equation}" in result["final_latex"]
        assert "\\label{eq:test}" in result["final_latex"]
        assert "E = mc^2" in result["final_latex"]
        assert "{{BLOCK_MATH_" not in result["final_latex"]


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
                "{{BLOCK_MATH_1}}": {"label": "eq:a", "content": "a = b"}
            },
            inline_dict={"{{INLINE_MATH_1}}": "F"},
        )
        result = python_merger(state)
        assert "$F$" in result["final_latex"]
        assert "\\begin{equation}" in result["final_latex"]
        assert "{{BLOCK_MATH_" not in result["final_latex"]
        assert "{{INLINE_MATH_" not in result["final_latex"]


class TestEmptyDicts:
    def test_no_formulas(self):
        state = _make_state(
            formatted_text="纯文字",
            formula_dict={},
            inline_dict={},
        )
        result = python_merger(state)
        assert result["final_latex"] == "纯文字"
