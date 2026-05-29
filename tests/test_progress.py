"""api.progress 阶段格式化器单元测试。"""
from api import progress


def test_phase_label_known_and_unknown():
    assert progress.phase_label("REVIEWING") == "多维审核"
    assert progress.phase_label("NOPE") == "NOPE"


def test_planning_formatter():
    out = progress.format_phase_output("PLANNING", {"planning_notes": "abc"})
    assert out["kind"] == "planning"
    assert out["planning_notes"]["text"] == "abc"
    assert out["planning_notes"]["truncated"] is False
    assert out["planning_notes"]["length"] == 3


def test_problem_formatter():
    out = progress.format_phase_output(
        "PROBLEM_GENERATING", {"title": "T", "problem_text": "body"}
    )
    assert out["kind"] == "problem"
    assert out["title"] == "T"
    assert out["problem_text"]["text"] == "body"


def test_review_formatter_marks_empty():
    out = progress.format_phase_output("REVIEWING", {
        "math_review": "ok", "physics_review": "", "quality_review": "   ",
    })
    by = {r["reviewer"]: r for r in out["reviews"]}
    assert set(by) == {"math", "physics", "structure", "quality"}
    assert by["math"]["empty"] is False
    assert by["physics"]["empty"] is True
    assert by["quality"]["empty"] is True  # 仅空白视为空
    assert by["structure"]["empty"] is True  # 缺字段


def test_arbitration_formatter():
    out = progress.format_phase_output("ARBITRATING", {
        "arbiter_decision": "RETRY_PROBLEM",
        "error_category": "fatal",
        "arbiter_reason": "r",
        "arbiter_feedback": "f",
        "problem_retry_count": 1,
        "solution_retry_count": 2,
        "retry_count": 3,
    })
    assert out["kind"] == "arbitration"
    assert out["decision"] == "RETRY_PROBLEM"
    assert out["retry"] == {"problem": 1, "solution": 2, "total": 3}


def test_formatting_counts():
    out = progress.format_phase_output("FORMATTING", {
        "formula_dict": {"a": 1, "b": 2},
        "inline_dict": {"x": 1},
        "figure_dict": {},
        "final_latex": "abcd",
    })
    assert out == {
        "kind": "formatting",
        "block_formula_count": 2,
        "inline_formula_count": 1,
        "figure_count": 0,
        "final_latex_chars": 4,
    }


def test_template_fixing_figure_keys():
    out = progress.format_phase_output("TEMPLATE_FIXING", {
        "template_report": "rep",
        "figure_descriptions": {"fig1": "...", "fig2": "..."},
    })
    assert out["kind"] == "template_fixing"
    assert out["figure_count"] == 2
    assert sorted(out["figure_keys"]) == ["fig1", "fig2"]


def test_truncation_applied():
    long_text = "x" * (progress.MAX_TEXT_CHARS + 100)
    out = progress.format_phase_output("PLANNING", {"planning_notes": long_text})
    field = out["planning_notes"]
    assert field["truncated"] is True
    assert field["length"] == progress.MAX_TEXT_CHARS + 100
    assert len(field["text"]) == progress.MAX_TEXT_CHARS


def test_missing_fields_are_defensive():
    # 完全空字典不应抛异常
    for phase in ("PLANNING", "PROBLEM_GENERATING", "SOLUTION_GENERATING",
                  "REVIEWING", "ARBITRATING", "FORMATTING", "TEMPLATE_FIXING",
                  "DONE", "ABORTED"):
        out = progress.format_phase_output(phase, {})
        assert isinstance(out, dict)
        assert "kind" in out


def test_unknown_phase_returns_generic():
    out = progress.format_phase_output("WAT", {})
    assert out == {"kind": "generic", "phase": "WAT"}


def test_formatter_exception_is_contained(monkeypatch):
    # 让某个格式化器抛错，验证被兜底为 error 结构
    def _boom(_data):
        raise ValueError("kaboom")

    monkeypatch.setitem(progress._FORMATTERS, "PLANNING", _boom)
    out = progress.format_phase_output("PLANNING", {})
    assert out["kind"] == "error"
    assert out["phase"] == "PLANNING"
    assert "kaboom" in out["message"]
