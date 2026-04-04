"""
正则隔离器单元测试。
覆盖: Block 提取、Inline 提取、混合提取、无公式、嵌套防护。
运行: python -m pytest tests/test_parser.py -v
"""
import pytest
from nodes.parser import python_parser


def _make_state(**overrides) -> dict:
    """构造最小化测试 state。"""
    base = {
        "topic": "", "difficulty": "", "draft_content": "",
        "math_review": "", "physics_review": "",
        "arbiter_decision": "", "arbiter_feedback": "",
        "retry_count": 0, "formula_dict": {}, "inline_dict": {},
        "tagged_text": "", "formatted_text": "", "final_latex": "",
    }
    base.update(overrides)
    return base


class TestBlockExtraction:
    """测试 Block 公式提取。"""

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
    """测试 Inline 公式提取。"""

    def test_single_inline(self):
        draft = "已知速度 $v_0$ 和加速度 $a$。"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["inline_dict"]) == 2
        assert "$" not in result["tagged_text"]

    def test_escaped_dollar_ignored(self):
        """转义的 \\$ 不应被匹配。"""
        draft = "价格是 \\$100，速度为 $v$。"
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["inline_dict"]) == 1


class TestMixedExtraction:
    """测试 Block + Inline 混合提取。"""

    def test_block_inline_coexist(self):
        draft = (
            '由 $F$ 和 $m$ 的关系：\n'
            '<block_math label="eq:f">\n'
            'F = ma\n'
            '</block_math>\n'
            '其中 $a$ 为加速度。'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 1  # 1 个 Block
        assert len(result["inline_dict"]) == 3    # 3 个 Inline ($F$, $m$, $a$)
        assert "<block_math" not in result["tagged_text"]
        assert "$" not in result["tagged_text"]


class TestEdgeCases:
    """边界情况测试。"""

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
    """测试 LLM 常见的标签格式错误修正。"""

    def test_latex_end_tag(self):
        """LLM 常把 </block_math> 错写为 \\end{block_math}"""
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
        """部分正确 + 部分错误闭合标签混合"""
        draft = (
            '<block_math label="eq:1">a = b</block_math>\n'
            '中间文字\n'
            '<block_math label="eq:2">\n'
            'c = d\n'
            '\\end{block_math}'
        )
        result = python_parser(_make_state(draft_content=draft))
        assert len(result["formula_dict"]) == 2
