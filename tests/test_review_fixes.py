import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from agents.arbiter import arbiter_agent
from agents.problem_generator import problem_generator_agent
from agents.reviewers import _structure_check
from client import UsageInfo
from client.openai_compat import OpenAICompatibleClient
from engine.state_machine import GenerationStateMachine
from model.schema import ArbiterDecision
from spec.normalizer import from_cli


def _tool_response(payload):
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps(payload)
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = [tool_call]
    resp.usage = None
    return resp


def _minimal_arbiter_state():
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "draft_content": "problem\n\nsolution",
            "math_review": "ok",
            "physics_review": "ok",
            "structure_review": "ok",
        }
    )
    return state


def test_arbiter_decision_rejects_invalid_decision_category_pairs():
    with pytest.raises(ValidationError):
        ArbiterDecision(
            decision="PASS",
            reason="valid content",
            feedback="valid content",
            error_category="fatal",
        )

    with pytest.raises(ValidationError):
        ArbiterDecision(
            decision="RETRY_PROBLEM",
            reason="valid content",
            feedback="valid content",
            error_category="style",
        )


@patch("agents.arbiter.get_client")
def test_arbiter_relabels_invalid_structured_tags(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client
    client.create.side_effect = [
        _tool_response(
            {
                "decision": "PASS",
                "reason": "ok",
                "feedback": "ok",
                "error_category": "fatal",
            }
        ),
        _tool_response(
            {
                "decision": "PASS",
                "reason": "ok",
                "feedback": "ok",
                "error_category": "none",
            }
        ),
    ]

    result = arbiter_agent(_minimal_arbiter_state())

    assert result["arbiter_decision"] == "PASS"
    assert result["error_category"] == "none"
    assert client.create.call_count == 2
    relabel_messages = client.create.call_args_list[1].kwargs["messages"]
    assert "PASS+none/style" in relabel_messages[-1]["content"]


def test_router_rejects_invalid_structured_tags():
    machine = GenerationStateMachine()
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "arbiter_decision": "PASS",
            "error_category": "fatal",
            "problem_retry_count": 0,
            "solution_retry_count": 0,
            "retry_count": 0,
        }
    )

    assert machine._route(state) == "end"


def test_router_rejects_pass_with_unknown_error_category():
    machine = GenerationStateMachine()
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "arbiter_decision": "PASS",
            "error_category": "weird",
            "problem_retry_count": 0,
            "solution_retry_count": 0,
            "retry_count": 0,
        }
    )

    assert machine._route(state) == "end"


def test_router_maps_pass_style_to_pass_with_edits():
    machine = GenerationStateMachine()
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "arbiter_decision": "PASS",
            "error_category": "style",
            "problem_retry_count": 0,
            "solution_retry_count": 0,
            "retry_count": 0,
        }
    )

    assert machine._route(state) == "pass_with_edits"


def test_router_rejects_unknown_decision():
    machine = GenerationStateMachine()
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "arbiter_decision": "RETRY",
            "error_category": "fatal",
            "problem_retry_count": 0,
            "solution_retry_count": 0,
            "retry_count": 0,
        }
    )

    assert machine._route(state) == "end"


@patch("agents.problem_generator.stream_chat")
@patch("agents.problem_generator.get_client")
def test_problem_generator_clears_stale_solution_on_problem_retry(
    mock_get_client,
    mock_stream_chat,
):
    mock_get_client.return_value = MagicMock()
    mock_stream_chat.return_value = ("Problem body", UsageInfo())
    state = from_cli(topic="topic", difficulty="medium", total_score=50)
    state.update(
        {
            "problem_retry_count": 1,
            "arbiter_feedback": "regenerate the problem",
            "problem_text": "old problem",
            "solution_text": "old solution",
            "math_review": "old math review",
            "physics_review": "old physics review",
            "structure_review": "old structure review",
            "quality_review": "old quality review",
        }
    )

    result = problem_generator_agent(state)

    assert result["solution_text"] == ""
    assert result["math_review"] == ""
    assert result["physics_review"] == ""
    assert result["structure_review"] == ""
    assert result["quality_review"] == ""


@patch("client.openai_compat.OpenAI")
def test_openai_compatible_client_passes_configured_max_retries(mock_openai):
    OpenAICompatibleClient(
        api_key="key",
        base_url="https://example.test/v1",
        timeout=12,
        max_retries=7,
    )

    assert mock_openai.call_args.kwargs["max_retries"] == 7


def test_structure_check_reports_many_leaf_questions_as_hint_for_40_points():
    state = from_cli(topic="topic", difficulty="medium", total_score=40)
    state.update(
        {
            "problem_text": "\n".join(
                [
                    "(1) 第一问",
                    "(1.1) 子问",
                    "(1.2) 子问",
                    "(2) 第二问",
                    "(2.1) 子问",
                    "(2.2) 子问",
                    "(3) 第三问",
                    "(3.1) 子问",
                    "(3.2) 子问",
                ]
            ),
            "solution_text": "\n".join(
                [
                    "(1.1)[5分] 解答",
                    "(1.2)[5分] 解答",
                    "(2.1)[5分] 解答",
                    "(2.2)[5分] 解答",
                    "(3.1)[10分] 解答",
                    "(3.2)[10分] 解答",
                ]
            ),
            "draft_content": "",
        }
    )

    result = _structure_check(state)

    assert "叶子小问 6 个" in result["structure_review"]
    assert "【结构检查通过】" in result["structure_review"]
    assert "题量提示" in result["structure_review"]


def test_structure_check_counts_only_deepest_question_nodes_as_leaves():
    state = from_cli(topic="topic", difficulty="medium", total_score=40)
    state.update(
        {
            "problem_text": "\n".join(
                [
                    "(1) 一级",
                    "(1.1) 二级",
                    "(1.1.1) 三级",
                ]
            ),
            "solution_text": "(1.1.1)[40分] 解答",
            "draft_content": "",
        }
    )

    result = _structure_check(state)

    assert "一级小问 1 个" in result["structure_review"]
    assert "叶子小问 1 个" in result["structure_review"]
    assert "叶子小问 2 个" not in result["structure_review"]


def test_structure_check_does_not_double_count_parent_and_leaf_scores():
    state = from_cli(topic="topic", difficulty="medium", total_score=40)
    state.update(
        {
            "problem_text": "\n".join(
                [
                    "(1) 第一问",
                    "(1.1) 子问",
                    "(1.2) 子问",
                    "(2) 第二问",
                    "(2.1) 子问",
                    "(2.2) 子问",
                ]
            ),
            "solution_text": "\n".join(
                [
                    "(1)[20分]",
                    "(1.1)[10分] 解答",
                    "(1.2)[10分] 解答",
                    "(2)[20分]",
                    "(2.1)[8分] 解答",
                    "(2.2)[12分] 解答",
                ]
            ),
            "draft_content": "",
        }
    )

    result = _structure_check(state)

    assert "【结构检查通过】" in result["structure_review"]
    assert "分值合计" not in result["structure_review"]


def test_structure_check_sums_mixed_parent_and_leaf_score_groups():
    state = from_cli(topic="topic", difficulty="medium", total_score=40)
    state.update(
        {
            "problem_text": "\n".join(
                [
                    "(1) 第一问",
                    "(1.1) 子问",
                    "(1.2) 子问",
                    "(2.1) 子问",
                    "(2.2) 子问",
                ]
            ),
            "solution_text": "\n".join(
                [
                    "(1)[10分]",
                    "(1.1)[4分] 解答",
                    "(1.2)[6分] 解答",
                    "(2.1)[15分] 解答",
                    "(2.2)[15分] 解答",
                ]
            ),
            "draft_content": "",
        }
    )

    result = _structure_check(state)

    assert "【结构检查通过】" in result["structure_review"]
    assert "分值合计" not in result["structure_review"]


def test_structure_check_accepts_fullwidth_question_numbers():
    state = from_cli(topic="topic", difficulty="medium", total_score=40)
    state.update(
        {
            "problem_text": "\n".join(
                [
                    "（1）第一问",
                    "（1.1）子问",
                    "（1.2）子问",
                    "（2）第二问",
                ]
            ),
            "solution_text": "\n".join(
                [
                    "（1）[20分]",
                    "（1.1）[8分] 解答",
                    "（1.2）[12分] 解答",
                    "（2）[20分] 解答",
                ]
            ),
            "draft_content": "",
        }
    )

    result = _structure_check(state)

    assert "【结构检查通过】" in result["structure_review"]
    assert "题干中未找到小问编号" not in result["structure_review"]
    assert "分值合计" not in result["structure_review"]
