"""
端到端集成测试（使用 Mock LLM，不消耗真实 API）。
测试整个图的路由逻辑：PASS 路径、RETRY 路径、ABORT 路径。
运行: python -m pytest tests/test_graph.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from graph.workflow import build_graph
from state.schema import AgentState


def _make_initial_state(**overrides) -> AgentState:
    base: AgentState = {
        "topic": "测试主题", "difficulty": "测试难度",
        "draft_content": "", "math_review": "", "physics_review": "",
        "arbiter_decision": "", "arbiter_feedback": "", "retry_count": 0,
        "formula_dict": {}, "inline_dict": {},
        "tagged_text": "", "formatted_text": "", "final_latex": "",
    }
    base.update(overrides)
    return base


def _make_stream_chunks(text: str):
    """构造 OpenAI 流式响应的 mock chunk 迭代器。"""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    stop = MagicMock()
    stop.choices = [MagicMock()]
    stop.choices[0].delta.content = None
    return iter([chunk, stop])


def _make_tool_call_response(decision: str, feedback: str):
    """构造 OpenAI Function Calling 响应的 mock。"""
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps(
        {"decision": decision, "feedback": feedback}
    )
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = [tool_call]
    return resp


class TestGraphRouting:
    """测试条件路由的三条路径。"""

    @patch("nodes.formatter.OpenAI")
    @patch("nodes.arbiter.OpenAI")
    @patch("nodes.physics_verifier.OpenAI")
    @patch("nodes.math_verifier.OpenAI")
    @patch("nodes.generator.OpenAI")
    def test_pass_path(self, mock_gen, mock_math, mock_phys, mock_arb, mock_fmt):
        """PASS 路径: generator → verifiers → arbiter(PASS) → parser → formatter → merger → END"""
        # Mock generator (streaming)
        gen_text = (
            '题干文字\n'
            '<block_math label="eq:1">F = ma</block_math>\n'
            '解答中 $v$ 表示速度。'
        )
        mock_gen.return_value.chat.completions.create.return_value = _make_stream_chunks(gen_text)

        # Mock verifiers (streaming)
        mock_math.return_value.chat.completions.create.return_value = _make_stream_chunks(
            "【数学审核通过】无数学错误。"
        )
        mock_phys.return_value.chat.completions.create.return_value = _make_stream_chunks(
            "【物理审核通过】无物理错误。"
        )

        # Mock arbiter (function calling, non-streaming)
        mock_arb.return_value.chat.completions.create.return_value = _make_tool_call_response(
            "PASS", "无需修改"
        )

        # Mock formatter (streaming)
        mock_fmt.return_value.chat.completions.create.return_value = _make_stream_chunks(
            "\\section*{题目}\n{{BLOCK_MATH_1}}\n速度 {{INLINE_MATH_1}}"
        )

        graph = build_graph()
        result = graph.invoke(_make_initial_state())

        assert result["arbiter_decision"] == "PASS"
        assert result["final_latex"] != ""
        assert "\\begin{equation}" in result["final_latex"]
