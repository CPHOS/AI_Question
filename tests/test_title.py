from utils.title import extract_leading_title, infer_title_from_content, is_noisy_title
from latex.isolate import isolate
from agents.problem_generator import _strip_thinking_chain


def test_extract_bracket_title_without_colon():
    title, body = extract_leading_title("【标题】磁弹性振子\n【题干】\n正文")

    assert title == "磁弹性振子"
    assert body == "【题干】\n正文"


def test_extract_bracket_title_with_colon():
    title, body = extract_leading_title("【标题】：带电粒子散射\n正文")

    assert title == "带电粒子散射"
    assert body == "正文"


def test_extract_plain_title_prefix():
    title, body = extract_leading_title("标题：刚体滚动\n正文")

    assert title == "刚体滚动"
    assert body == "正文"


def test_extract_markdown_h1_title():
    title, body = extract_leading_title("# 动态衍射光栅\n正文")

    assert title == "动态衍射光栅"
    assert body == "正文"


def test_extract_title_keeps_default_when_absent():
    title, body = extract_leading_title("【题干】\n正文", default="原题")

    assert title == "原题"
    assert body == "【题干】\n正文"


def test_noisy_title_detection_for_exported_pdf_stem():
    assert is_noisy_title("导出页面自 Quantum Electronics for Atomic Physics")
    assert is_noisy_title("Physics_Model_Big_Wheel_Pendulum")
    assert not is_noisy_title("一维黑体辐射与 Johnson 噪声")


def test_infer_title_from_johnson_noise_content():
    title = infer_title_from_content(
        "本题旨在通过一个一维光子气体的模型，从电磁场量子化的角度，探讨 Johnson-Nyquist 热噪声。"
        "考虑一个理想的无损耗传输线和 LC 阶梯网络。",
        "导出页面自 Quantum Electronics for Atomic Physics",
    )

    assert title == "一维黑体辐射与 Johnson 噪声"


def test_isolate_uses_shared_title_parser():
    state = {
        "title": "",
        "draft_content": "标题：带电粒子散射\n【题干】\n正文",
    }

    result = isolate(state)

    assert result["title"] == "带电粒子散射"
    assert result["tagged_text"] == "【题干】\n正文"


def test_isolate_extracts_bracket_title():
    state = {
        "title": "",
        "draft_content": "【标题】磁弹性振子\n【题干】\n某物理系统...",
    }

    result = isolate(state)

    assert result["title"] == "磁弹性振子"
    assert "【标题】" not in result["tagged_text"]


def test_isolate_infers_title_when_input_has_no_title():
    state = {
        "title": "",
        "topic": "导出页面自 Quantum Electronics for Atomic Physics",
        "draft_content": "【题干】\nJohnson-Nyquist 热噪声可以通过一维光子气体和传输线模型解释。",
    }

    result = isolate(state)

    assert result["title"] == "一维黑体辐射与 Johnson 噪声"


def test_problem_generator_strip_thinking_keeps_title():
    content = "analysis text\n【标题】：热噪声\n【题干】\n正文"

    stripped = _strip_thinking_chain(content)
    title, body = extract_leading_title(stripped)

    assert title == "热噪声"
    assert body == "【题干】\n正文"
