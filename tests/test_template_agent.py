from latex.template_agent import fix_template


def test_template_check_rejects_commented_figure():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "%\\begin{figure}[H]\n"
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

    result = fix_template({"final_latex": latex})

    assert "figure 环境仍处于注释状态" in result["template_report"]


def test_template_auto_fixes_markdown_residue():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "**\\subq{1} 题**\n"
        "\\end{problemstatement}\n"
        "\\begin{solution}\n"
        "\\solsubq{1}{10}\n"
        "\\begin{equation}x=1 \\eqtagscore{1}{10}\\end{equation}\n"
        "\\scoring\n"
        "\\end{solution}\n"
        "\\end{problem}\n"
        "\\end{document}\n"
    )

    result = fix_template({"final_latex": latex})

    assert "**" not in result["final_latex"]


def test_template_markdown_cleanup_preserves_literal_double_underscore():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "**变量** a__b 保持原样，__强调__ 去掉标记。\n"
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

    result = fix_template({"final_latex": latex})

    assert "a__b" in result["final_latex"]
    assert "变量" in result["final_latex"]
    assert "__强调__" not in result["final_latex"]


def test_template_normalizes_compacted_mathrm_d():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "\\subq{1} 求 $\\mathrmd N/\\mathrmd\\nu$ 与 $\\mathrmdx$。\n"
        "\\end{problemstatement}\n"
        "\\begin{solution}\n"
        "\\solsubq{1}{10}\n"
        "\\begin{equation}\\mathrmd N+\\mathrmdx=1 \\eqtagscore{1}{10}\\end{equation}\n"
        "\\scoring\n"
        "\\end{solution}\n"
        "\\end{problem}\n"
        "\\end{document}\n"
    )

    result = fix_template({"final_latex": latex})

    assert "\\mathrmd" not in result["final_latex"]
    assert "\\mathrm{d} N/\\mathrm{d}\\nu" in result["final_latex"]
    assert "\\mathrm{d}x" in result["final_latex"]


def test_template_check_rejects_missing_scoring_points():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "\\subq{1} 题\n"
        "\\end{problemstatement}\n"
        "\\begin{solution}\n"
        "\\solsubq{1}{10}\n"
        "解答没有可解析评分点。\n"
        "\\scoring\n"
        "\\end{solution}\n"
        "\\end{problem}\n"
        "\\end{document}\n"
    )

    result = fix_template({"final_latex": latex})

    assert "缺少 \\eqtagscore 或 \\addtext 评分点" in result["template_report"]
    assert "叶子解答 1缺少可解析评分点" in result["template_report"]


def test_template_check_rejects_missing_third_level_solution():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "\\subq{1} 一级\n"
        "\\subsubq{1.1} 二级\n"
        "\\subsubsubq{1.1.1} 三级\n"
        "\\end{problemstatement}\n"
        "\\begin{solution}\n"
        "\\solsubq{1}{10}\n"
        "\\solsubsubq{1.1}{10}\n"
        "\\begin{equation}x=1 \\eqtagscore{1}{10}\\end{equation}\n"
        "\\scoring\n"
        "\\end{solution}\n"
        "\\end{problem}\n"
        "\\end{document}\n"
    )

    result = fix_template({"final_latex": latex})

    assert "解答缺少三级小问 ['1.1.1']" in result["template_report"]


def test_template_check_rejects_residual_generation_tags():
    latex = (
        "\\documentclass[answer]{cphos}\n"
        "\\begin{document}\n"
        "\\begin{problem}[10]{测试}\n"
        "\\begin{problemstatement}\n"
        "\\subq{1} 题\n"
        "\\end{problemstatement}\n"
        "\\begin{solution}\n"
        "\\solsubq{1}{10}\n"
        "<block_math score=\"10\">x=1</block_math>\n"
        "\\scoring\n"
        "\\end{solution}\n"
        "\\end{problem}\n"
        "\\end{document}\n"
    )

    result = fix_template({"final_latex": latex})

    assert "存在未回填的生成标签残留" in result["template_report"]
