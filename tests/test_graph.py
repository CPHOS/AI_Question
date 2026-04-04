"""
端到端集成测试（使用 Mock LLM，不消耗真实 API）。
测试整个图的路由逻辑：PASS 路径、RETRY 路径、ABORT 路径。
运行: python -m pytest tests/test_graph.py -v
"""
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


class TestGraphRouting:
    """测试条件路由的三条路径。"""

    @patch("nodes.formatter.ChatOpenAI")
    @patch("nodes.arbiter.ChatOpenAI")
    @patch("nodes.physics_verifier.ChatOpenAI")
    @patch("nodes.math_verifier.ChatOpenAI")
    @patch("nodes.generator.ChatOpenAI")
    def test_pass_path(self, mock_gen, mock_math, mock_phys, mock_arb, mock_fmt):
        """PASS 路径: generator → verifiers → arbiter(PASS) → parser → formatter → merger → END"""
        # Mock generator
        gen_response = MagicMock()
        gen_response.content = (
            '题干文字\n'
            '<block_math label="eq:1">F = ma</block_math>\n'
            '解答中 $v$ 表示速度。'
        )
        mock_gen.return_value.invoke.return_value = gen_response

        # Mock verifiers
        math_resp = MagicMock()
        math_resp.content = "【数学审核通过】无数学错误。"
        mock_math.return_value.invoke.return_value = math_resp

        phys_resp = MagicMock()
        phys_resp.content = "【物理审核通过】无物理错误。"
        mock_phys.return_value.invoke.return_value = phys_resp

        # Mock arbiter (with_structured_output)
        arb_resp = MagicMock()
        arb_resp.decision = "PASS"
        arb_resp.feedback = "无需修改"
        mock_arb.return_value.with_structured_output.return_value.invoke.return_value = arb_resp

        # Mock formatter
        fmt_response = MagicMock()
        fmt_response.content = "\\section*{题目}\n{{BLOCK_MATH_1}}\n速度 {{INLINE_MATH_1}}"
        mock_fmt.return_value.invoke.return_value = fmt_response

        graph = build_graph()
        result = graph.invoke(_make_initial_state(), config={"recursion_limit": 30})

        assert result["arbiter_decision"] == "PASS"
        assert result["final_latex"] != ""
        assert "\\begin{equation}" in result["final_latex"]
