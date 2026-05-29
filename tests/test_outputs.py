import os
import subprocess

from app import _append_test_log
from app.outputs import (
    _build_texinputs,
    _build_tikz_stub,
    _resolve_template_dir,
    _run_latex_compile,
    _rewrite_figure_asset_paths,
    write_outputs,
)
from model.stats import clear, record


def test_write_outputs_creates_tikz_stub(tmp_path, monkeypatch):
    monkeypatch.setattr("app.outputs.AUTO_COMPILE_FIGURES", False)
    state = {
        "figure_descriptions": {
            "fig_1": {
                "filename": "fig1.pdf",
                "tikz_filename": "fig1.tex",
                "caption": "系统示意图",
                "description": "画一根导电细杆",
            }
        }
    }

    paths = write_outputs("task", state, tmp_path)

    tikz_path = tmp_path / "task_assets" / "fig1.tex"
    readme_path = tmp_path / "task_assets" / "README.md"
    tikz_content = tikz_path.read_text(encoding="utf-8")
    readme_content = readme_path.read_text(encoding="utf-8")

    assert tikz_path.exists()
    assert readme_path.exists()
    assert paths["fig_1_tikz"] == tikz_path
    assert "\\documentclass[tikz" in tikz_content
    assert "\\begin{tikzpicture}" in tikz_content
    assert "画一根导电细杆" in tikz_content
    assert "fig1.tex" in readme_content


def test_build_tikz_stub_uses_lc_cell_for_t_model():
    tex = _build_tikz_stub(
        "fig_1",
        {
            "caption": "传输线的 T 型 LC 单元模型",
            "description": "画出一个由两个 L0/2 串联电感和一个 C0 并联电容构成的 T 型单元。",
        },
    )

    assert "T 型 LC 单元" in tex
    assert "$L_0/2$" in tex
    assert "$C_0$" in tex
    assert "$R_1,T_1$" not in tex


def test_write_outputs_rewrites_figure_asset_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.outputs.AUTO_COMPILE_FIGURES", False)
    monkeypatch.setattr("app.outputs.AUTO_COMPILE_LATEX", False)
    state = {
        "final_latex": (
            "\\begin{problem}[10]{测试}\n"
            "\\begin{problemstatement}\n"
            "\\begin{figure}[H]\n"
            "\\includegraphics{fig/fig1.pdf}\n"
            "\\end{figure}\n"
            "\\subq{1}题\n"
            "\\end{problemstatement}\n"
            "\\begin{solution}\\solsubq{1}{10}解\\scoring\\end{solution}\n"
            "\\end{problem}\n"
        ),
        "figure_descriptions": {
            "fig_1": {
                "filename": "fig1.pdf",
                "tikz_filename": "fig1.tex",
                "caption": "系统示意图",
                "description": "画一根导电细杆",
            }
        },
    }

    paths = write_outputs("taskabc", state, tmp_path)
    final_tex = paths["final_latex"].read_text(encoding="utf-8")

    assert "插图 PDF 未生成：fig1.pdf" in final_tex
    assert "{taskabc_assets/fig1.pdf}" not in final_tex


def test_write_outputs_rewrites_figure_asset_path_when_pdf_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("app.outputs.AUTO_COMPILE_FIGURES", False)
    monkeypatch.setattr("app.outputs.AUTO_COMPILE_LATEX", False)
    state = {
        "final_latex": (
            "\\begin{problem}[10]{测试}\n"
            "\\begin{problemstatement}\n"
            "\\begin{figure}[H]\n"
            "\\includegraphics{fig/fig1.pdf}\n"
            "\\end{figure}\n"
            "\\subq{1}题\n"
            "\\end{problemstatement}\n"
            "\\begin{solution}\\solsubq{1}{10}解\\scoring\\end{solution}\n"
            "\\end{problem}\n"
        ),
        "figure_descriptions": {
            "fig_1": {
                "filename": "fig1.pdf",
                "tikz_filename": "fig1.tex",
                "caption": "系统示意图",
                "description": "画一根导电细杆",
            }
        },
    }
    assets_dir = tmp_path / "taskabc_assets"
    assets_dir.mkdir()
    (assets_dir / "fig1.pdf").write_bytes(b"%PDF-1.4\n")

    final_tex = _rewrite_figure_asset_paths(
        state["final_latex"],
        "taskabc",
        state["figure_descriptions"],
        {"fig1.pdf"},
    )

    assert "{taskabc_assets/fig1.pdf}" in final_tex


def test_resolve_template_dir_relative_to_project_root(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    template_dir = tmp_path / "CPHOS-Latex" / "theory"
    project_root.mkdir()
    template_dir.mkdir(parents=True)
    monkeypatch.setattr("app.outputs.PROJECT_ROOT", project_root)

    result = _resolve_template_dir("../CPHOS-Latex/theory")

    assert result == str(template_dir.resolve())


def test_build_texinputs_preserves_default_search_path():
    result = _build_texinputs("template-dir")

    assert result == os.pathsep.join([".", "template-dir", ""])


def test_run_latex_compile_reports_timeout(tmp_path, monkeypatch):
    tex_path = tmp_path / "fig1.tex"
    tex_path.write_text("\\documentclass{standalone}\\begin{document}x\\end{document}", encoding="utf-8")

    monkeypatch.setattr("app.outputs.shutil.which", lambda name: "xelatex")

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("app.outputs.subprocess.run", _timeout)

    ok, detail = _run_latex_compile(tex_path)

    assert ok is False
    assert "timed out" in detail
    assert "fig1.tex" in detail


def test_run_latex_compile_reports_start_failure(tmp_path, monkeypatch):
    tex_path = tmp_path / "fig1.tex"
    tex_path.write_text("\\documentclass{standalone}\\begin{document}x\\end{document}", encoding="utf-8")

    monkeypatch.setattr("app.outputs.shutil.which", lambda name: "xelatex")

    def _start_failure(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr("app.outputs.subprocess.run", _start_failure)

    ok, detail = _run_latex_compile(tex_path)

    assert ok is False
    assert "failed to start" in detail
    assert "denied" in detail


def test_append_test_log_includes_quality_check_node(tmp_path, monkeypatch):
    monkeypatch.setattr("app.PROJECT_ROOT", tmp_path)
    clear()
    record("math_check", 10, 1.0, prompt_tokens=1, completion_tokens=2, total_tokens=3)
    record("physics_check", 11, 1.0, prompt_tokens=1, completion_tokens=2, total_tokens=3)
    record("quality_check", 12, 1.0, prompt_tokens=1, completion_tokens=2, total_tokens=3)

    try:
        _append_test_log(
            topic="topic",
            difficulty="medium",
            model="model",
            max_tokens=100,
            total_elapsed=1.0,
            final_state={},
            error_msg="",
        )
    finally:
        clear()

    text = (tmp_path / "TEST_LOG.md").read_text(encoding="utf-8")

    assert "- math_check: 10 字符" in text
    assert "- physics_check: 11 字符" in text
    assert "- quality_check: 12 字符" in text
