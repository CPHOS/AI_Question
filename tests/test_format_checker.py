"""FormatChecker 封装与 fix_template 集成测试。"""
import pytest

from latex.format_checker import (
    FormatCheckResult,
    run_format_check,
    format_check_section,
)


_GOOD_LATEX = (
    "\\documentclass[answer]{cphos}\n"
    "\\begin{document}\n"
    "\\begin{problem}[10]{测试}\n"
    "\\begin{problemstatement}\n"
    "\\subq{1} 题\n"
    "\\end{problemstatement}\n"
    "\\begin{solution}\n"
    "\\solsubq{1}{10}\n"
    "\\begin{equation}x=1 \\eqtagscore{1}{10}\\end{equation}\n"
    "\\scoring\n"
    "\\end{solution}\n"
    "\\end{problem}\n"
    "\\end{document}\n"
)


# ── 封装层（子进程 + 降级）────────────────────────────────────────────────

def test_run_format_check_disabled_returns_unavailable(monkeypatch):
    from config import config as cfg

    monkeypatch.setattr(cfg, "FORMAT_CHECK_ENABLED", False)
    result = run_format_check(_GOOD_LATEX)

    assert result.available is False
    assert "未启用" in result.report


def test_run_format_check_missing_submodule_degrades(monkeypatch, tmp_path):
    from config import config as cfg

    monkeypatch.setattr(cfg, "FORMAT_CHECK_ENABLED", True)
    monkeypatch.setattr(cfg, "FORMAT_CHECK_DIR", str(tmp_path / "does-not-exist"))
    result = run_format_check(_GOOD_LATEX)

    assert result.available is False
    assert "不可用" in result.report
    assert result.error_count == 0


def test_run_format_check_real_submodule(monkeypatch):
    """有 submodule 时，应能真实运行并返回结构化计数。"""
    from pathlib import Path
    from config import config as cfg

    if not (Path(cfg.FORMAT_CHECK_DIR) / "main.py").is_file():
        pytest.skip("FormatChecker submodule 未初始化")

    monkeypatch.setattr(cfg, "FORMAT_CHECK_ENABLED", True)
    monkeypatch.setattr(cfg, "FORMAT_CHECK_MIN_SEVERITY", "warning")
    result = run_format_check(_GOOD_LATEX)

    assert result.available is True
    # 该文档缺少 cphostitle/cphossubtitle 元数据，应至少触发警告。
    assert result.warning_count >= 1
    assert "检查文件" not in result.report  # 横幅/临时路径已剔除
    assert "document.tex" not in result.report


# ── 报告渲染 ───────────────────────────────────────────────────────────────

def test_format_check_section_unavailable():
    section = format_check_section(FormatCheckResult(False, "未启用"))
    assert "已跳过" in section
    assert "未启用" in section


def test_format_check_section_clean():
    section = format_check_section(FormatCheckResult(True, "", 0, 0, 0))
    assert "未发现格式问题" in section


def test_format_check_section_with_issues():
    result = FormatCheckResult(True, "#1 [STRUCT-002] 警告 缺少元数据", 1, 2, 0)
    section = format_check_section(result)
    assert "发现 1 个错误, 2 个警告, 0 个信息" in section
    assert "STRUCT-002" in section


# ── fix_template 集成（并入 template_report）───────────────────────────────

def test_fix_template_appends_format_check_feedback(monkeypatch):
    import latex.template_agent as ta

    canned = FormatCheckResult(True, "#1 [STRUCT-002] 警告 缺少元数据", 0, 1, 0)
    monkeypatch.setattr(ta, "run_format_check", lambda _latex: canned)

    result = ta.fix_template({"final_latex": _GOOD_LATEX})

    report = result["template_report"]
    assert report.startswith("模板检查通过，无需修正。")
    assert "外部格式检查 (FormatChecker)" in report
    assert "STRUCT-002" in report


def test_fix_template_omits_section_when_checker_unavailable(monkeypatch):
    import latex.template_agent as ta

    monkeypatch.setattr(
        ta, "run_format_check", lambda _latex: FormatCheckResult(False, "未启用")
    )

    result = ta.fix_template({"final_latex": _GOOD_LATEX})

    assert result["template_report"] == "模板检查通过，无需修正。"
    assert "FormatChecker" not in result["template_report"]
