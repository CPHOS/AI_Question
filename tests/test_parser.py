"""
正则隔离器单元测试。
运行: uv run pytest tests/test_parser.py -v
"""
import pytest
from latex.isolate import isolate as python_parser


def _make_state(**overrides) -> dict:
    """构造最小化测试 state。"""
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


class TestBlockExtraction:
    def test_single_block(self):
        draft = (
            '文字说明\n'
            '<block_math label="eq:newton">\n'
            'F = ma\n'
            '</block_math>\n'
            '后续文字'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 1
        placeholder = list(result["formula_dict"].keys())[0]
        assert "BLOCK_MATH" in placeholder
        assert result["formula_dict"][placeholder]["content"] == "F = ma"
        assert result["formula_dict"][placeholder]["label"] == "eq:newton"
        assert placeholder in result["tagged_text"]
        assert "<block_math" not in result["tagged_text"]

    def test_multiple_blocks(self):
        draft = (
            '<block_math label="eq:1">a = b</block_math>\n'
            '中间文字\n'
            '<block_math label="eq:2">c = d</block_math>'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 2


class TestInlineExtraction:
    def test_single_inline(self):
        draft = "已知速度 $v_0$ 和加速度 $a$。"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["inline_dict"]) == 2
        assert "$" not in result["tagged_text"]

    def test_escaped_dollar_ignored(self):
        draft = "价格是 \\$100，速度为 $v$。"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["inline_dict"]) == 1


class TestMixedExtraction:
    def test_block_inline_coexist(self):
        draft = (
            '由 $F$ 和 $m$ 的关系：\n'
            '<block_math label="eq:f">\n'
            'F = ma\n'
            '</block_math>\n'
            '其中 $a$ 为加速度。'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 1
        assert len(result["inline_dict"]) == 3
        assert "<block_math" not in result["tagged_text"]
        assert "$" not in result["tagged_text"]


class TestEdgeCases:
    def test_no_formulas(self):
        draft = "这是一段没有任何公式的纯文字。"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 0
        assert len(result["inline_dict"]) == 0
        assert result["tagged_text"] == draft

    def test_empty_input(self):
        result = python_parser(_make_state(draft_content=""))
        assert result["tagged_text"] == ""


class TestMalformedTags:
    def test_latex_end_tag(self):
        draft = (
            '文字\n'
            '<block_math label="eq:test">\n'
            'E = mc^2\n'
            '\\end{block_math}\n'
            '后续'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 1
        placeholder = list(result["formula_dict"].keys())[0]
        assert result["formula_dict"][placeholder]["content"] == "E = mc^2"
        assert "\\end{block_math}" not in result["tagged_text"]

    def test_mixed_correct_and_wrong_tags(self):
        draft = (
            '<block_math label="eq:1">a = b</block_math>\n'
            '中间文字\n'
            '<block_math label="eq:2">\n'
            'c = d\n'
            '\\end{block_math}'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 2


class TestScoreExtraction:
    def test_block_with_score(self):
        draft = (
            '解答\n'
            '<block_math label="eq:force" score="3">\n'
            'F = ma\n'
            '</block_math>\n'
            '后续'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 1
        placeholder = list(result["formula_dict"].keys())[0]
        assert result["formula_dict"][placeholder]["score"] == "3"
        assert result["formula_dict"][placeholder]["content"] == "F = ma"

    def test_block_without_score(self):
        draft = '<block_math label="eq:setup">E = mc^2</block_math>'
        result = python_parser(_make_state(draft_content=draft))
        placeholder = list(result["formula_dict"].keys())[0]
        assert result["formula_dict"][placeholder]["score"] == ""


class TestFigureExtraction:
    def test_single_figure(self):
        draft = (
            '如图\\ref{fig:setup}所示\n'
            '<figure label="fig:setup" caption="系统示意图">\n'
            '画一根导电细杆\n'
            '</figure>\n'
            '后续文字'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["figure_dict"]) == 1
        ph = list(result["figure_dict"].keys())[0]
        assert "FIGURE" in ph
        assert result["figure_dict"][ph]["label"] == "fig:setup"
        assert result["figure_dict"][ph]["caption"] == "系统示意图"
        assert "导电细杆" in result["figure_dict"][ph]["description"]
        assert "<figure" not in result["tagged_text"]
        assert ph in result["tagged_text"]

    def test_no_figures(self):
        draft = "纯文字无图"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["figure_dict"]) == 0


class TestTitleExtraction:
    def test_title_extracted(self):
        draft = "【标题】磁弹性振子\n【题干】\n某物理系统..."
        result = python_parser(_make_state(draft_content=draft))
        assert result["title"] == "磁弹性振子"
        assert "【标题】" not in result["tagged_text"]

    def test_no_title(self):
        draft = "【题干】\n某物理系统..."
        result = python_parser(_make_state(draft_content=draft))
        assert "title" not in result
