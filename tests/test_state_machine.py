"""
状态机集成测试（使用 Mock LLM，不消耗真实 API）。
运行: uv run pytest tests/test_state_machine.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from engine.state_machine import build_graph, Phase
from spec.normalizer import from_cli
from client import UsageInfo
from model.stats import clear as _clear_stats


_ZERO_USAGE = UsageInfo()


@pytest.fixture(autouse=True)
def _reset_run_stats():
    """每个测试前清空 model.stats 模块状态，避免跨测试污染 arbiter_r* 计数。"""
    _clear_stats()
    yield
    _clear_stats()


def _make_initial_state(**overrides):
    state = from_cli(topic="测试主题", difficulty="测试难度", total_score=50)
    state.update(overrides)
    return state


def _make_tool_call_response(decision: str, feedback: str, reason: str = "",
                             error_category: str = "none"):
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


class TestStateMachineRouting:
    """测试状态机条件路由。"""

    @patch("latex.format.stream_chat")
    @patch("latex.format.get_client")
    @patch("agents.arbiter.get_client")
    @patch("agents.reviewers.stream_chat")
    @patch("agents.reviewers.get_client")
    @patch("agents.solution_generator.stream_chat")
    @patch("agents.solution_generator.get_client")
    @patch("agents.problem_generator.stream_chat")
    @patch("agents.problem_generator.get_client")
    @patch("spec.planner.stream_chat")
    @patch("spec.planner.get_client")
    def test_pass_path(
        self, mock_plan_client, mock_plan_chat,
        mock_prob_client, mock_prob_chat,
        mock_sol_client, mock_sol_chat,
        mock_rev_client, mock_rev_chat,
        mock_arb_client,
        mock_fmt_client, mock_fmt_chat,
    ):
        """PASS 路径完整流程"""
        mock_plan_chat.return_value = ("规划: 3个小问，涉及刚体力学", _ZERO_USAGE)

        problem_text = (
            '【标题】测试题\n【题干】\n物理情境\n'
            '(1) 第一问\n(2) 第二问\n'
        )
        mock_prob_chat.return_value = (problem_text, _ZERO_USAGE)

        solution_text = (
            '(1)[25分]\n'
            '<block_math label="eq:1" score="10">F = ma</block_math>\n'
            '(2)[25分]\n'
            '<block_math label="eq:2" score="10">E = mc^2</block_math>\n'
        )
        mock_sol_chat.return_value = (solution_text, _ZERO_USAGE)

        mock_rev_chat.return_value = ("【数学审核通过】无数学错误。", _ZERO_USAGE)

        mock_arb_client.return_value.create.return_value = _make_tool_call_response(
            "PASS", "无需修改"
        )

        mock_fmt_chat.return_value = (
            "\\documentclass[answer]{cphos}\n\\begin{document}\n"
            "\\begin{problem}{测试题}\n\\begin{problemstatement}\n"
            "题目\n\\end{problemstatement}\n"
            "\\begin{solution}\n{{BLOCK_MATH_1}}\n{{BLOCK_MATH_2}}\n"
            "\\end{solution}\n\\end{problem}\n\\end{document}",
            _ZERO_USAGE,
        )

        sm = build_graph()
        result = sm.run(_make_initial_state())

        assert result["arbiter_decision"] == "PASS"
        assert result["final_latex"] != ""
        assert sm.phase == Phase.DONE
        # 首轮通过：不应有任何 retry
        assert result["problem_retry_count"] == 0
        assert result["solution_retry_count"] == 0
        assert result["retry_count"] == 0

    @patch("latex.format.stream_chat")
    @patch("latex.format.get_client")
    @patch("agents.arbiter.get_client")
    @patch("agents.reviewers.stream_chat")
    @patch("agents.reviewers.get_client")
    @patch("agents.solution_generator.stream_chat")
    @patch("agents.solution_generator.get_client")
    @patch("agents.problem_generator.stream_chat")
    @patch("agents.problem_generator.get_client")
    @patch("spec.planner.stream_chat")
    @patch("spec.planner.get_client")
    def test_retry_counters_split_by_stage(
        self, mock_plan_client, mock_plan_chat,
        mock_prob_client, mock_prob_chat,
        mock_sol_client, mock_sol_chat,
        mock_rev_client, mock_rev_chat,
        mock_arb_client,
        mock_fmt_client, mock_fmt_chat,
    ):
        """分阶段重试计数：RETRY_PROBLEM 与 RETRY_SOLUTION 各自独立累加，
        PASS 不递增，最终 problem_text 应重生成 2 次、solution_text 3 次。"""
        mock_plan_chat.return_value = ("规划", _ZERO_USAGE)

        problem_text = '【标题】测试\n【题干】\n(1) 第一问\n'
        mock_prob_chat.return_value = (problem_text, _ZERO_USAGE)

        solution_text = (
            '(1)[50分]\n<block_math label="eq:1" score="20">F=ma</block_math>\n'
        )
        mock_sol_chat.return_value = (solution_text, _ZERO_USAGE)

        mock_rev_chat.return_value = ("审核意见", _ZERO_USAGE)

        # 仲裁序列：RETRY_PROBLEM → RETRY_SOLUTION → RETRY_SOLUTION → PASS
        mock_arb_client.return_value.create.side_effect = [
            _make_tool_call_response("RETRY_PROBLEM", "题干问题", error_category="fatal"),
            _make_tool_call_response("RETRY_SOLUTION", "解答问题", error_category="fatal"),
            _make_tool_call_response("RETRY_SOLUTION", "解答仍有误", error_category="fatal"),
            _make_tool_call_response("PASS", "通过"),
        ]

        mock_fmt_chat.return_value = (
            "\\documentclass[answer]{cphos}\n\\begin{document}\n"
            "\\begin{problem}{测试}\n\\begin{problemstatement}\n题\n"
            "\\end{problemstatement}\n\\begin{solution}\n{{BLOCK_MATH_1}}\n"
            "\\end{solution}\n\\end{problem}\n\\end{document}",
            _ZERO_USAGE,
        )

        sm = build_graph()
        result = sm.run(_make_initial_state())

        assert result["arbiter_decision"] == "PASS"
        # 1 次 RETRY_PROBLEM，2 次 RETRY_SOLUTION
        assert result["problem_retry_count"] == 1
        assert result["solution_retry_count"] == 2
        assert result["retry_count"] == 3
        # 问题被生成 2 次（首轮 + 1 次 RETRY_PROBLEM）
        assert mock_prob_chat.call_count == 2
        # 解答被生成 4 次（每轮题干重生后都会重解 + 2 次 RETRY_SOLUTION）
        assert mock_sol_chat.call_count == 4
        # arbiter 统计：4 次仲裁应生成 4 个独立 key，不因 PASS 不递增 retry 而覆盖
        from model.stats import get_all as _get_stats
        arb_keys = sorted(k for k in _get_stats() if k.startswith("arbiter_r"))
        assert arb_keys == ["arbiter_r1", "arbiter_r2", "arbiter_r3", "arbiter_r4"]
