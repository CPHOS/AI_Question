"""
CPHOS FormatChecker 封装（git submodule，位于 external/FormatChecker）。

在模板修正节点（:func:`latex.template_agent.fix_template`）对 ``final_latex``
运行外部格式检查器，并把人类可读的检查报告作为反馈并入 ``template_report``，
供后续格式修整流程参考。

设计要点
========
- **子进程隔离**：通过 ``python main.py <tex> --no-color --no-cache`` 调用 CLI，
  并把 ``cwd`` 设为 submodule 根目录（其内部 ``from src.checker import Checker``
  依赖该工作目录）。如此既能让 FormatChecker 的顶层包 ``src`` 正常解析，又不会把
  它注入本项目的 ``sys.modules`` 造成命名污染。
- **无副作用**：``--no-cache`` 禁用缓存写入；被检查文本写入临时目录，调用后清理。
- **优雅降级**：未启用 / submodule 缺失 / 子进程异常 / 超时 → 返回
  ``available=False``，绝不抛出，不阻断生成流水线。

所有可调参数（启用开关、目录、级别、超时）均来自 :mod:`config.config` 的部署项，
不在本模块硬编码魔术值。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config.config import logger


# 合法的最低报告级别（与 FormatChecker CLI ``--min-severity`` 取值一致）。
_VALID_SEVERITIES = ("info", "warning", "error")

# CLI 汇总行：``--- 汇总: 1 个错误, 2 个警告, 3 个信息 ---``
_SUMMARY_RE = re.compile(r"(\d+)\s*个错误[,，]\s*(\d+)\s*个警告[,，]\s*(\d+)\s*个信息")
# CLI 无问题提示。
_NO_ISSUE_MARK = "未发现格式问题"
# 报告中需要剔除的样板行（横幅、临时文件路径、汇总分隔行），
# 避免泄露临时路径、与我们自渲染的计数行重复。
_BANNER_PREFIXES = ("===", "---", "检查文件:", "检查文件：")


@dataclass(frozen=True)
class FormatCheckResult:
    """一次格式检查的结果。

    :ivar available: 检查器是否成功运行（False 表示未启用或降级）。
    :ivar report: 人类可读的检查报告（available=False 时为降级原因说明）。
    :ivar error_count/warning_count/info_count: 各级别问题数。
    """
    available: bool
    report: str
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0


def _clean_report(stdout: str) -> str:
    """剔除横幅与临时文件路径行，得到精炼反馈文本。"""
    lines = [
        ln for ln in stdout.splitlines()
        if ln.strip() and not ln.lstrip().startswith(_BANNER_PREFIXES)
    ]
    return "\n".join(lines).strip()


def run_format_check(latex: str) -> FormatCheckResult:
    """对 ``latex`` 运行 FormatChecker，返回 :class:`FormatCheckResult`。

    任何失败都被吞掉并以 ``available=False`` 表达，调用方据此优雅降级。
    """
    from config import config as cfg

    if not cfg.FORMAT_CHECK_ENABLED:
        return FormatCheckResult(False, "FormatChecker 未启用。")

    main_py = Path(cfg.FORMAT_CHECK_DIR) / "main.py"
    if not main_py.is_file():
        logger.warning("[format-check] 未找到 FormatChecker（%s），跳过格式检查。", main_py)
        return FormatCheckResult(
            False, f"FormatChecker 不可用：未找到 {main_py}（git submodule 未初始化？）。"
        )

    severity = str(cfg.FORMAT_CHECK_MIN_SEVERITY).strip().lower()
    if severity not in _VALID_SEVERITIES:
        severity = "warning"

    with tempfile.TemporaryDirectory(prefix="fmtcheck_") as tmp:
        tex_path = Path(tmp) / "document.tex"
        tex_path.write_text(latex, encoding="utf-8")
        cmd = [
            sys.executable, str(main_py), str(tex_path),
            "--no-color", "--no-cache", "--min-severity", severity,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(main_py.parent),
                capture_output=True,
                text=True,
                timeout=float(cfg.FORMAT_CHECK_TIMEOUT),
            )
        except subprocess.TimeoutExpired:
            logger.warning("[format-check] 格式检查超时，跳过。")
            return FormatCheckResult(False, "FormatChecker 运行超时。")
        except Exception as exc:  # noqa: BLE001 — 降级语义，绝不向上抛
            logger.warning("[format-check] 格式检查异常：%s", exc)
            return FormatCheckResult(False, f"FormatChecker 运行异常：{exc}")

    stdout = proc.stdout or ""
    if proc.returncode not in (0, 1):
        # 0 = 无错误；1 = 存在 error 级问题。其余为 CLI 自身异常（参数/文件错误等）。
        detail = (proc.stderr or stdout).strip()
        logger.warning("[format-check] FormatChecker 退出码 %d：%s", proc.returncode, detail)
        return FormatCheckResult(False, f"FormatChecker 执行失败（退出码 {proc.returncode}）。")

    match = _SUMMARY_RE.search(stdout)
    if match:
        errors, warnings, infos = (int(match.group(i)) for i in (1, 2, 3))
    elif _NO_ISSUE_MARK in stdout:
        errors = warnings = infos = 0
    else:
        # 无法解析汇总时，用退出码兜底判断是否存在 error。
        errors, warnings, infos = (1 if proc.returncode == 1 else 0), 0, 0

    return FormatCheckResult(
        available=True,
        report=_clean_report(stdout),
        error_count=errors,
        warning_count=warnings,
        info_count=infos,
    )


def format_check_section(result: FormatCheckResult) -> str:
    """把检查结果渲染为并入 ``template_report`` 的一节文本。"""
    header = "外部格式检查 (FormatChecker)："
    if not result.available:
        return f"{header}\n  已跳过（{result.report}）"
    if result.error_count == result.warning_count == result.info_count == 0:
        return f"{header}\n  通过，未发现格式问题。"
    counts = (
        f"  发现 {result.error_count} 个错误, "
        f"{result.warning_count} 个警告, {result.info_count} 个信息。"
    )
    body = "\n".join("  " + ln for ln in result.report.splitlines())
    return f"{header}\n{counts}\n{body}"
