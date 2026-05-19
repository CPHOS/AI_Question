from latex.format import _clean_placeholder_braces


def test_clean_placeholder_braces_preserves_addtext_score_brace():
    text = r"\addtext{利用经典近似 {{INLINE_MATH_1}}}{1}"

    cleaned = _clean_placeholder_braces(text)

    assert cleaned == text


def test_clean_placeholder_braces_removes_single_wrapper():
    text = r"公式 {{{INLINE_MATH_1}}} 结束"

    cleaned = _clean_placeholder_braces(text)

    assert cleaned == r"公式 {{INLINE_MATH_1}} 结束"
