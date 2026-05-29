import os
import subprocess

from app.outputs import (
    BuildStep,
    LocalCompiler,
    RemoteCompiler,
    _build_texinputs,
    _build_tikz_stub,
    _get_compiler,
    _resolve_template_dir,
    _run_latex_compile,
    _rewrite_figure_asset_paths,
    recompile_outputs,
    write_outputs,
)


def test_write_outputs_creates_tikz_stub(tmp_path, monkeypatch):
    monkeypatch.setattr("config.runtime.auto_compile_figures", lambda: False)
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
    monkeypatch.setattr("config.runtime.auto_compile_figures", lambda: False)
    monkeypatch.setattr("config.runtime.auto_compile_latex", lambda: False)
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
    monkeypatch.setattr("config.runtime.auto_compile_figures", lambda: False)
    monkeypatch.setattr("config.runtime.auto_compile_latex", lambda: False)
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


def test_get_compiler_selects_backend(monkeypatch):
    monkeypatch.setattr("config.runtime.latex_compiler_backend", lambda: "local")
    assert isinstance(_get_compiler(), LocalCompiler)
    monkeypatch.setattr("config.runtime.latex_compiler_backend", lambda: "remote")
    assert isinstance(_get_compiler(), RemoteCompiler)


def test_local_compiler_returns_produced_pdf(tmp_path, monkeypatch):
    tex = tmp_path / "fig1.tex"
    tex.write_text("x", encoding="utf-8")

    def _fake_compile(path, *, cphos_template_dir="", timeout=None):
        path.with_suffix(".pdf").write_bytes(b"%PDF")
        return True, "ok"

    monkeypatch.setattr("app.outputs._run_latex_compile", _fake_compile)
    results = LocalCompiler().compile_many(tmp_path, [BuildStep("fig1", "fig1.tex", 1)])

    assert results["fig1"].ok is True
    assert results["fig1"].produced == ("fig1.pdf",)


def test_local_compiler_passes_count(tmp_path, monkeypatch):
    tex = tmp_path / "doc.tex"
    tex.write_text("x", encoding="utf-8")
    calls = {"n": 0}

    def _fake_compile(path, *, cphos_template_dir="", timeout=None):
        calls["n"] += 1
        path.with_suffix(".pdf").write_bytes(b"%PDF")
        return True, "ok"

    monkeypatch.setattr("app.outputs._run_latex_compile", _fake_compile)
    LocalCompiler().compile_many(tmp_path, [BuildStep("final", "doc.tex", 2)])

    assert calls["n"] == 2


def test_local_compiler_stops_on_failure(tmp_path, monkeypatch):
    tex = tmp_path / "doc.tex"
    tex.write_text("x", encoding="utf-8")
    calls = {"n": 0}

    def _fake_compile(path, *, cphos_template_dir="", timeout=None):
        calls["n"] += 1
        return False, "err"

    monkeypatch.setattr("app.outputs._run_latex_compile", _fake_compile)
    results = LocalCompiler().compile_many(tmp_path, [BuildStep("final", "doc.tex", 2)])

    assert calls["n"] == 1  # 第一遍失败即停止
    assert results["final"].ok is False
    assert results["final"].produced == ()


def test_remote_backend_never_calls_subprocess(tmp_path, monkeypatch):
    """backend=remote 时编译完全走 RemoteCompiler，不触本机 subprocess。"""
    monkeypatch.setattr("config.runtime.latex_compiler_backend", lambda: "remote")
    monkeypatch.setattr("config.runtime.auto_compile_figures", lambda: True)
    monkeypatch.setattr("config.runtime.auto_compile_latex", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("subprocess.run 不应被调用")

    monkeypatch.setattr("app.outputs.subprocess.run", _boom)

    captured = {}

    def _fake_compile_many(self, workspace, steps, *, assets=(), template_dir="", timeout=None):
        captured.setdefault("steps", []).extend(s.id for s in steps)
        return {s.id: __import__("app.outputs", fromlist=["StepResult"]).StepResult(s.id, False, "stub") for s in steps}

    monkeypatch.setattr("app.outputs.RemoteCompiler.compile_many", _fake_compile_many)

    state = {
        "final_latex": "\\begin{problem}[10]{t}\\begin{problemstatement}\\subq{1}a\\end{problemstatement}\\begin{solution}\\solsubq{1}{10}b\\scoring\\end{solution}\\end{problem}",
        "figure_descriptions": {
            "fig_1": {"filename": "fig1.pdf", "tikz_filename": "fig1.tex", "caption": "c", "description": "d"},
        },
    }

    write_outputs("taskx", state, tmp_path)

    assert "fig_1" in captured["steps"]
    assert "final" in captured["steps"]


def test_recompile_outputs_missing_final_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        recompile_outputs("task", tmp_path)


def test_recompile_outputs_compiles_figures_and_final(tmp_path, monkeypatch):
    # 准备磁盘产物：final tex + 一个图片 stub + 既有 log.json
    (tmp_path / "task_final.tex").write_text("\\documentclass{article}", encoding="utf-8")
    assets = tmp_path / "task_assets"
    assets.mkdir()
    (assets / "fig1.tex").write_text("\\documentclass{standalone}", encoding="utf-8")
    (tmp_path / "task_log.json").write_text(
        '{"latex_compile_status": {}, "figure_compile_status": {}}', encoding="utf-8"
    )

    def _fake_compile(path, *, cphos_template_dir="", timeout=None):
        path.with_suffix(".pdf").write_bytes(b"%PDF")
        return True, "ok"

    monkeypatch.setattr("config.runtime.latex_compiler_backend", lambda: "local")
    monkeypatch.setattr("app.outputs._run_latex_compile", _fake_compile)

    status = recompile_outputs("task", tmp_path)

    assert status["latex_compile_status"]["ok"] is True
    assert status["figure_compile_status"]["fig1"]["ok"] is True
    assert (tmp_path / "task_final.pdf").exists()
    assert (assets / "fig1.pdf").exists()

    # log.json 的编译状态被回写
    import json
    log = json.loads((tmp_path / "task_log.json").read_text(encoding="utf-8"))
    assert log["latex_compile_status"]["ok"] is True
    assert log["figure_compile_status"]["fig1"]["ok"] is True
