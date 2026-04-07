"""
端到端集成测试（使用 Mock LLM，不消耗真实 API）。
运行: uv run pytest tests/test_graph.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from graph.workflow import build_graph
from model.state import AgentState
from client import UsageInfo


_ZERO_USAGE = UsageInfo()


def _make_initial_state(**overrides) -> AgentState:
    base: AgentState = {
        "topic": "测试主题", "difficulty": "测试难度",
        "total_score": 50, "title": "",
        "draft_content": "", "math_review": "", "physics_review": "",
        "arbiter_decision": "", "arbiter_reason": "", "arbiter_feedback": "",
        "error_category": "",
        "retry_count": 0,
        "formula_dict": {}, "inline_dict": {}, "figure_dict": {},
        "tagged_text": "", "formatted_text": "", "final_latex": "",
        "figure_descriptions": {},
    }
    base.update(overrides)
    return base


def _make_tool_call_response(decision: str, feedback: str, reason: str = "",
                             error_category: str = "none"):
    """构造 OpenAI Function Calling 响应的 mock。"""
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps(
        {"decision": decision, "reason": reason or feedback,
         "feedback": feedback, "error_category": error_category}
    )
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = [tool_call]
    resp.usage = None
    return resp


class TestGraphRouting:
    """测试条件路由的三条路径。"""

    @patch("formatter.formatter.stream_chat")
    @patch("formatter.formatter.get_client")
    @patch("generator.arbiter.get_client")
    @patch("generator.physics_verifier.stream_chat")
    @patch("generator.physics_verifier.get_client")
    @patch("generator.math_verifier.stream_chat")
    @patch("generator.math_verifier.get_client")
    @patch("generator.generator.stream_chat")
    @patch("generator.generator.get_client")
    def test_pass_path(self, mock_gen_client, mock_gen_chat,
                       mock_math_client, mock_math_chat,
                       mock_phys_client, mock_phys_chat,
                       mock_arb_client,
                       mock_fmt_client, mock_fmt_chat):
        """PASS 路径: generator → verifiers → arbiter(PASS) → parser → formatter → merger → END"""
        gen_text = (
            '题干文字\n'
            '<block_math label="eq:1">F = ma</block_math>\n'
            '解答中 $v$ 表示速度。'
        )
        mock_gen_chat.return_value = (gen_text, _ZERO_USAGE)
        mock_math_chat.return_value = ("【数学审核通过】无数学错误。", _ZERO_USAGE)
        mock_phys_chat.return_value = ("【物理审核通过】无物理错误。", _ZERO_USAGE)

        mock_arb_client.return_value.create.return_value = _make_tool_call_response(
            "PASS", "无需修改"
        )

        mock_fmt_chat.return_value = (
            "\\documentclass[answer]{cphos}\n\\begin{document}\n"
            "\\begin{problem}{}\n\\begin{problemstatement}\n"
            "题目\n\\end{problemstatement}\n"
            "\\begin{solution}\n{{BLOCK_MATH_1}}\n速度 {{INLINE_MATH_1}}\n"
            "\\end{solution}\n\\end{problem}\n\\end{document}",
            _ZERO_USAGE,
        )

        graph = build_graph()
        result = graph.invoke(_make_initial_state())

        assert result["arbiter_decision"] == "PASS"
        assert result["final_latex"] != ""
        assert "\\begin{equation}" in result["final_latex"]
