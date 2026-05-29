"""
产物写盘与 LaTeX / 图片编译辅助。

本模块从 :mod:`app` 主入口抽出，集中负责把状态机的最终状态
（:class:`~model.state.WorkflowData`）渲染为磁盘产物：LaTeX 成品、草稿、
占位文本、图片绘制需求与 TikZ 草稿、运行日志 JSON 与仲裁报告。

所有写盘函数都接收 ``output_dir`` 参数，因此既能服务 CLI（写扁平的
``OUTPUT_DIR``），也能服务多用户 API 后端（写 ``OUTPUT_DIR/{user_id}``）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from model.state import WorkflowData
from config.config import (
    logger, PROJECT_ROOT, LATEX_ENGINE, CPHOS_TEMPLATE_DIR,
)
from config import runtime
from latex.template_spec import FIGURE_WIDTH


# ============ 产物文件名后缀（单一事实来源） ============
# 逻辑名 → 文件名后缀。:mod:`api.jobs` 反转此表枚举/定位产物，避免两处各写一份。
ARTIFACT_SUFFIXES: dict[str, str] = {
    "final_latex": "_final.tex",
    "final_pdf": "_final.pdf",
    "draft": "_draft.md",
    "tagged": "_tagged.md",
    "log": "_log.json",
    "report": "_report.md",
}
# 每任务资产子目录后缀（图片 PDF / TikZ 源 / README）。
ASSETS_DIR_SUFFIX: str = "_assets"

# 额度查询子进程超时上限（秒）：避免 budget 快照查询拖慢主流程。
_BUDGET_QUERY_TIMEOUT_CAP: int = 30



# ============ LaTeX 文本转义与 TikZ 草稿 ============

def _tex_escape_text(text: str) -> str:
    """转义可放进普通 LaTeX 文本节点的说明文字。"""
    mapping = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(mapping.get(ch, ch) for ch in text)


def _build_tikz_stub(fig_name: str, data: dict) -> str:
    """生成可独立编译为 PDF 的 TikZ 草稿文件。"""
    caption = _tex_escape_text(data.get("caption", ""))
    raw = f"{data.get('caption', '')}\n{data.get('description', '')}"
    preamble = [
        "\\documentclass[tikz,border=6pt]{standalone}\n",
        "\\usepackage{ctex}\n",
        "\\usetikzlibrary{arrows.meta,calc,decorations.pathmorphing,patterns}\n",
        "\\begin{document}\n",
        "% Auto-generated TikZ draft for AI_Question.\n",
        f"% Figure: {fig_name}\n",
        f"% Caption: {caption}\n",
        "\\begin{tikzpicture}[x=1cm,y=1cm,line cap=round,line join=round,>=Latex]\n",
    ]
    ending = ["\\end{tikzpicture}\n", "\\end{document}\n"]

    if any(key in raw for key in ("圆环", "摆锤", "摆杆", "変径", "变径")):
        body = [
            "    \\draw[very thick] (-3.8,-1.25) -- (3.8,-1.25) node[right] {ground};\n",
            "    \\draw[thick] (0,0) circle (1.25);\n",
            "    \\fill (0,0) circle (1.3pt) node[above left] {$O$};\n",
            "    \\draw[dashed] (0,0) -- (0,-1.85);\n",
            "    \\draw[thick] (0,0) -- (0.82,-1.55) node[midway,right] {$L$};\n",
            "    \\fill (0.82,-1.55) circle (4pt) node[right] {$m$};\n",
            "    \\draw[->] (0,-0.58) arc[start angle=-90,end angle=-62,radius=0.58];\n",
            "    \\node at (0.42,-0.78) {$\\theta$};\n",
            "    \\draw[<->] (0,0) -- (1.25,0) node[midway,above] {$R$};\n",
            "    \\draw[->,thick] (-3.0,0.2) -- (-1.7,0.2) node[midway,above] {$X$};\n",
            "    \\node[align=center,font=\\small] at (0,1.65) {变径摆驱动圆环};\n",
        ]
        return "".join(preamble + body + ending)

    if any(key in raw for key in ("LC", "T型", "T 型", "阶梯网络", "电容", "电感")):
        body = [
            "    \\draw[very thick] (-4.0,0) -- (-2.2,0);\n",
            "    \\draw[very thick] (2.2,0) -- (4.0,0);\n",
            "    \\draw[very thick] (-2.2,0) -- (-1.25,0);\n",
            "    \\draw[very thick] (1.25,0) -- (2.2,0);\n",
            "    \\draw[thick] (-1.25,-0.23) -- (-1.25,0.23);\n",
            "    \\draw[thick] (-0.95,-0.23) -- (-0.95,0.23);\n",
            "    \\draw[thick] (0.95,-0.23) -- (0.95,0.23);\n",
            "    \\draw[thick] (1.25,-0.23) -- (1.25,0.23);\n",
            "    \\node[above] at (-1.1,0.36) {$L_0/2$};\n",
            "    \\node[above] at (1.1,0.36) {$L_0/2$};\n",
            "    \\draw[very thick] (0,0) -- (0,-0.65);\n",
            "    \\draw[thick] (-0.42,-0.65) -- (0.42,-0.65);\n",
            "    \\draw[thick] (-0.42,-0.92) -- (0.42,-0.92);\n",
            "    \\draw[very thick] (0,-0.92) -- (0,-1.45);\n",
            "    \\draw[very thick] (-3.6,-1.45) -- (3.6,-1.45);\n",
            "    \\node[right] at (0.46,-0.78) {$C_0$};\n",
            "    \\draw[<->] (-2.2,0.85) -- (2.2,0.85) node[midway,above] {$l_0$};\n",
            "    \\node[font=\\small] at (0,-1.9) {T 型 LC 单元};\n",
        ]
        return "".join(preamble + body + ending)

    if any(key in raw for key in ("传输线", "电阻", "热噪声", "阻抗")):
        body = [
            "    \\draw[very thick] (-3.0,0) -- (3.0,0);\n",
            "    \\draw[decorate,decoration={zigzag,segment length=4pt,amplitude=2pt},thick] (-4.1,0) -- (-3.0,0);\n",
            "    \\draw[decorate,decoration={zigzag,segment length=4pt,amplitude=2pt},thick] (3.0,0) -- (4.1,0);\n",
            "    \\node[above] at (-3.55,0.24) {$R_1,T_1$};\n",
            "    \\node[above] at (3.55,0.24) {$R_2,T_2$};\n",
            "    \\draw[<->] (-3.0,0.82) -- (3.0,0.82) node[midway,above] {$L$};\n",
            "    \\draw[->,thick] (-1.2,0.18) -- (1.2,0.18) node[midway,above] {$v$};\n",
            "    \\node[font=\\small] at (0,-0.45) {$Z_0=R_1=R_2$};\n",
            "    \\node[align=center,font=\\small] at (0,1.55) {一维传输线与端接电阻};\n",
        ]
        return "".join(preamble + body + ending)

    description = _tex_escape_text(re.sub(r"\s+", " ", data.get("description", "")[:220]).strip())
    lines = preamble + [
        "    \\draw[thick, rounded corners=2pt] (-3.2,-1.6) rectangle (3.2,1.6);\n",
        "    \\draw[->, thick] (-2.6,-0.9) -- (-1.2,-0.9) node[midway, below] {$x$};\n",
        "    \\draw[->, thick] (1.2,-0.9) -- (2.6,-0.9) node[midway, below] {$v$};\n",
        f"    \\node[align=center, text width=5.6cm] at (0,0.45) {{{caption}}};\n",
        f"    \\node[align=center, text width=5.8cm, font=\\small] at (0,-0.45) {{{description}}};\n",
    ] + ending
    return "".join(lines)


# ============ LaTeX 编译 ============

def _run_latex_compile(
    tex_path: Path, *, cphos_template_dir: str = "", timeout: int | None = None
) -> tuple[bool, str]:
    """编译单个 tex 文件，返回 (是否成功, 诊断信息)。"""
    engine = shutil.which(LATEX_ENGINE)
    if not engine:
        return False, f"未找到 LaTeX 引擎: {LATEX_ENGINE}"

    if timeout is None:
        timeout = runtime.latex_compile_timeout()

    env = os.environ.copy()
    if cphos_template_dir:
        env["TEXINPUTS"] = _build_texinputs(cphos_template_dir, env.get("TEXINPUTS", ""))

    try:
        proc = subprocess.run(
            [engine, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=tex_path.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"LaTeX compile timed out after {exc.timeout}s: {tex_path.name}"
    except OSError as exc:
        return False, f"LaTeX compile failed to start: {exc}"
    if proc.returncode == 0:
        return True, "compiled"
    tail = "\n".join(proc.stdout.splitlines()[-20:])
    return False, tail


def _build_texinputs(cphos_template_dir: str, existing: str = "") -> str:
    """构造 TEXINPUTS，末尾空项保留 TeX 发行版默认搜索路径。"""
    texinputs = [".", cphos_template_dir]
    if existing:
        texinputs.append(existing)
    texinputs.append("")
    return os.pathsep.join(texinputs)


def _resolve_template_dir(path_value: str) -> str:
    """把模板目录解析为绝对路径，供 output/ 内的 LaTeX 子进程使用。"""
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    return str(path) if path.exists() else ""


def _cleanup_latex_aux(tex_path: Path) -> None:
    """清理单图编译产生的辅助文件。"""
    for suffix in (".aux", ".out", ".toc", ".fls", ".fdb_latexmk", ".synctex.gz"):
        aux = tex_path.with_suffix(suffix)
        if aux.exists():
            aux.unlink()


# ============ 编译后端抽象（local / remote） ============
#
# 历史上 write_outputs 直接调本机 subprocess（``_run_latex_compile``）。为支持把
# 编译卸载到独立服务，这里抽出统一的「构建请求」接口：一次调用提交一组有序步骤，
# 各后端自行决定如何执行（本机串行 / 远程单作业多步骤），并把产出的 PDF 落到
# ``workspace`` 内对应相对路径。两端对外返回结构一致的 :class:`StepResult`，因此
# write_outputs 的产物分发与状态记录逻辑与后端无关。

LATEX_BACKEND_LOCAL = "local"
LATEX_BACKEND_REMOTE = "remote"


@dataclass(frozen=True)
class BuildStep:
    """一个编译步骤：对 ``root``（workspace 相对路径）编译 ``passes`` 遍。"""

    id: str
    root: str
    passes: int = 1


@dataclass(frozen=True)
class StepResult:
    """单步编译结果。``produced`` 为产出文件的 workspace 相对路径。"""

    id: str
    ok: bool
    log_tail: str = ""
    produced: tuple[str, ...] = ()


def _pdf_rel_of(root_rel: str) -> str:
    """由 root 的相对路径推出其 PDF 产物相对路径（同目录、同名 .pdf）。"""
    return str(Path(root_rel).with_suffix(".pdf"))


class LocalCompiler:
    """本机后端：在 ``workspace`` 原地用 subprocess 串行编译各步。"""

    def compile_many(
        self,
        workspace: Path,
        steps: list[BuildStep],
        *,
        assets: tuple[str, ...] = (),
        template_dir: str = "",
        timeout: int | None = None,
    ) -> dict[str, StepResult]:
        # 本机后端的资产（assets）已在磁盘上，无需额外处理。
        results: dict[str, StepResult] = {}
        for step in steps:
            tex_path = workspace / step.root
            ok, detail = False, ""
            for _ in range(max(1, step.passes)):
                ok, detail = _run_latex_compile(
                    tex_path, cphos_template_dir=template_dir, timeout=timeout
                )
                if not ok:
                    break
            _cleanup_latex_aux(tex_path)
            produced: tuple[str, ...] = ()
            pdf_path = tex_path.with_suffix(".pdf")
            if ok and pdf_path.exists():
                produced = (_pdf_rel_of(step.root),)
            results[step.id] = StepResult(step.id, ok, detail, produced)
        return results


class RemoteCompiler:
    """远程后端：把全部步骤打包成单个编译作业提交、轮询、回收产物。"""

    def compile_many(
        self,
        workspace: Path,
        steps: list[BuildStep],
        *,
        assets: tuple[str, ...] = (),
        template_dir: str = "",
        timeout: int | None = None,
    ) -> dict[str, StepResult]:
        from client.latex_service import LatexServiceClient, LatexServiceError

        base_url = runtime.latex_service_base_url()
        if not base_url:
            return {
                s.id: StepResult(s.id, False, "LATEX_SERVICE_BASE_URL 未配置")
                for s in steps
            }
        if timeout is None:
            timeout = runtime.latex_compile_timeout()

        # 收集工作区文件：步骤 root（tex 源）+ 额外资产（如已编译的图片 PDF）。
        files: dict[str, bytes] = {}
        for rel in [s.root for s in steps] + list(assets):
            fp = workspace / rel
            if fp.exists():
                files[rel] = fp.read_bytes()

        manifest = {
            "engine": LATEX_ENGINE,
            "timeout_seconds": timeout,
            "steps": [{"id": s.id, "root": s.root, "passes": s.passes} for s in steps],
            "outputs": [_pdf_rel_of(s.root) for s in steps],
        }

        try:
            with LatexServiceClient(base_url) as client:
                job = client.submit(files, manifest)
                job = client.wait(
                    job.job_id,
                    poll_interval=runtime.latex_service_poll_interval(),
                    max_wait=runtime.latex_service_max_wait(),
                )
                if job.status == "failed":
                    detail = (job.error or {}).get("message", "远程编译作业失败")
                    results = {s.id: StepResult(s.id, False, str(detail)) for s in steps}
                    client.delete(job.job_id)
                    return results
                if job.status == "expired":
                    return {s.id: StepResult(s.id, False, "远程编译作业已过期") for s in steps}

                results = self._collect(client, job, steps, workspace)
                client.delete(job.job_id)
                return results
        except LatexServiceError as exc:
            return {
                s.id: StepResult(s.id, False, f"远程编译失败: {exc}") for s in steps
            }

    def _collect(self, client, job, steps: list[BuildStep], workspace: Path) -> dict[str, StepResult]:
        """把作业的步骤结果转为 StepResult，并把产物 PDF 下载回工作区。"""
        from client.latex_service import LatexServiceError

        step_by_id = {st.get("id"): st for st in job.steps}
        results: dict[str, StepResult] = {}
        for step in steps:
            st = step_by_id.get(step.id)
            if st is None:
                results[step.id] = StepResult(step.id, False, "服务未返回该步骤结果")
                continue
            ok = bool(st.get("ok"))
            log_tail = str(st.get("log_tail", ""))
            produced = tuple(st.get("produced", []))
            if ok:
                for rel in produced:
                    try:
                        data = client.fetch_artifact(job.job_id, rel)
                    except LatexServiceError as exc:
                        ok = False
                        log_tail += f"\n[产物下载失败] {rel}: {exc}"
                        continue
                    dest = workspace / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            results[step.id] = StepResult(step.id, ok, log_tail, produced)
        return results


def _get_compiler():
    """按运行期设置选择编译后端。"""
    if runtime.latex_compiler_backend() == LATEX_BACKEND_REMOTE:
        return RemoteCompiler()
    return LocalCompiler()


def _rewrite_figure_asset_paths(
    latex: str,
    task_id: str,
    fig_desc: dict,
    available_pdfs: set[str] | None = None,
) -> str:
    """把 merge 阶段的 fig/figN.pdf 路径改为本任务资产目录；缺失 PDF 时改为可编译占位。"""
    result = latex
    for data in fig_desc.values():
        filename = data.get("filename", "")
        if filename:
            src = f"fig/{filename}"
            if available_pdfs is None or filename in available_pdfs:
                result = result.replace(f"{{{src}}}", f"{{{task_id}{ASSETS_DIR_SUFFIX}/{filename}}}")
            else:
                placeholder = (
                    rf"\fbox{{\parbox{{{FIGURE_WIDTH}}}{{\centering "
                    f"插图 PDF 未生成：{_tex_escape_text(filename)}"
                    r"}}"
                )
                result = re.sub(
                    rf'\\includegraphics(?:\[[^\]]*\])?\{{{re.escape(src)}\}}',
                    lambda _m: placeholder,
                    result,
                )
    return result


# ============ API 额度查询 ============

def budget_snapshot() -> dict:
    """查询当前 API 额度快照，失败时返回错误对象供报告记录。"""
    creds = runtime.openrouter_credentials()
    if creds is None:
        return {"provider": "openrouter", "available": False,
                "error": "未配置 OpenRouter 服务商凭据"}
    api_key, timeout = creds
    try:
        from client.openrouter import query_openrouter_credits

        data = query_openrouter_credits(api_key, timeout=min(timeout, _BUDGET_QUERY_TIMEOUT_CAP))
        data["provider"] = "openrouter"
        data["available"] = True
        return data
    except Exception as exc:
        return {
            "provider": "openrouter",
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def budget_delta_usd(start: dict, end: dict) -> float | None:
    """根据 OpenRouter 余额快照计算本题美元消耗。"""
    if not (start.get("available") and end.get("available")):
        return None
    if isinstance(start.get("balance"), (int, float)) and isinstance(end.get("balance"), (int, float)):
        return round(float(start["balance"]) - float(end["balance"]), 6)
    if isinstance(start.get("total_usage"), (int, float)) and isinstance(end.get("total_usage"), (int, float)):
        return round(float(end["total_usage"]) - float(start["total_usage"]), 6)
    return None


# ============ 编译辅助（写盘与按需编译共用） ============

def _compile_final_document(
    task_id: str, output_dir: Path, available_figure_pdfs: set[str],
) -> tuple[dict, Path | None]:
    """编译最终文档（``passes=2``，带 CPHOS 模板与图片 PDF 资产）。

    无视 ``AUTO_COMPILE_LATEX`` 开关——是否调用由上层决定；本函数只负责执行。

    Returns:
        ``(latex_compile_status, final_pdf_path | None)``。
    """
    final_tex = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['final_latex']}"
    if not final_tex.exists():
        return {"ok": False, "detail": "final_latex 不存在", "template_dir": CPHOS_TEMPLATE_DIR}, None
    template_dir = _resolve_template_dir(CPHOS_TEMPLATE_DIR)
    fig_assets = tuple(
        str(Path(f"{task_id}{ASSETS_DIR_SUFFIX}") / name)
        for name in sorted(available_figure_pdfs)
    )
    results = _get_compiler().compile_many(
        output_dir,
        [BuildStep(id="final", root=final_tex.name, passes=2)],
        assets=fig_assets,
        template_dir=template_dir,
    )
    result = results["final"]
    status = {"ok": result.ok, "detail": result.log_tail, "template_dir": template_dir}
    pdf = final_tex.with_suffix(".pdf")
    return status, (pdf if result.ok and pdf.exists() else None)


def _update_log_compile_status(
    task_id: str, output_dir: Path, latex_status: dict, figure_status: dict,
) -> None:
    """把最新编译状态写回 ``_log.json``（若存在），作为 API 暴露的单一数据源。"""
    log_path = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['log']}"
    if not log_path.exists():
        return
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    data["latex_compile_status"] = latex_status
    data["figure_compile_status"] = figure_status
    log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def recompile_outputs(task_id: str, output_dir: Path) -> dict:
    """对磁盘上已有的最终 LaTeX 与图片 TikZ 源**重新编译**（不调用 LLM）。

    用于按需生成 / 刷新 PDF：典型场景是生成时 ``AUTO_COMPILE_LATEX=false`` 未产出
    PDF，事后由用户显式触发编译。流程：

    1. 重编 ``{task}_assets/`` 下的 ``figN.tex`` standalone 源 → ``figN.pdf``；
    2. 以可用图片 PDF 为资产重编最终文档（``passes=2``）；
    3. 把最新编译状态写回 ``_log.json``。

    注意：最终 LaTeX 中的图片引用 / 占位在生成时已根据当时可用的图片确定，本函数
    不重写引用路径；若需要据新编译出的图片刷新引用，应重跑生成任务。

    Returns:
        ``{"latex_compile_status": {...}, "figure_compile_status": {...}}``。

    Raises:
        FileNotFoundError: 任务无 ``final_latex`` 产物，无可编译对象。
    """
    final_tex = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['final_latex']}"
    if not final_tex.exists():
        raise FileNotFoundError(f"任务 {task_id} 无 final_latex 产物，无法编译")

    compiler = _get_compiler()
    figure_compile_status: dict[str, dict] = {}
    available_figure_pdfs: set[str] = set()

    assets_dir = output_dir / f"{task_id}{ASSETS_DIR_SUFFIX}"
    if assets_dir.is_dir():
        fig_steps = [
            BuildStep(id=tex.stem, root=tex.name, passes=1)
            for tex in sorted(assets_dir.glob("*.tex"))
        ]
        if fig_steps:
            for fig_id, result in compiler.compile_many(assets_dir, fig_steps).items():
                figure_compile_status[fig_id] = {"ok": result.ok, "detail": result.log_tail}
                pdf = assets_dir / f"{fig_id}.pdf"
                if result.ok and pdf.exists():
                    available_figure_pdfs.add(pdf.name)

    latex_compile_status, _ = _compile_final_document(task_id, output_dir, available_figure_pdfs)
    _update_log_compile_status(task_id, output_dir, latex_compile_status, figure_compile_status)
    return {
        "latex_compile_status": latex_compile_status,
        "figure_compile_status": figure_compile_status,
    }


# ============ 产物写盘 ============

def write_outputs(task_id: str, final_state: WorkflowData, output_dir: Path) -> dict[str, Path]:
    """将 final_state 的关键内容写入 ``output_dir`` 目录。

    Args:
        task_id: 任务标识，作为产物文件名前缀。
        final_state: 状态机执行后的最终状态。
        output_dir: 产物落盘目录（CLI 用 ``OUTPUT_DIR``；API 用每用户子目录）。

    Returns:
        产物名称到磁盘路径的映射（如 ``final_latex`` / ``draft`` / ``log`` / ``report``）。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    fig_desc = final_state.get("figure_descriptions", {})
    latex_compile_status: dict[str, str | bool] = {}
    figure_compile_status: dict[str, dict[str, str | bool]] = {}
    available_figure_pdfs: set[str] = set()

    if final_state.get("draft_content"):
        p = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['draft']}"
        p.write_text(final_state["draft_content"], encoding="utf-8")
        paths["draft"] = p

    if final_state.get("tagged_text"):
        p = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['tagged']}"
        p.write_text(final_state["tagged_text"], encoding="utf-8")
        paths["tagged"] = p

    # ===== 图片绘制需求与外置 PDF 资产 =====
    if fig_desc:
        assets_dir = output_dir / f"{task_id}{ASSETS_DIR_SUFFIX}"
        assets_dir.mkdir(exist_ok=True)
        lines = ["# 图片绘制需求\n\n"]
        fig_steps: list[BuildStep] = []  # 一次性提交的图片编译步骤
        step_to_fig: dict[str, str] = {}  # 步骤 id → 图片键
        for fig_name in sorted(fig_desc.keys()):
            data = fig_desc[fig_name]
            lines.append(f"## {data['filename']} — {data['caption']}\n\n")
            if data.get("tikz_filename"):
                lines.append(f"- TikZ 源文件: `{data['tikz_filename']}`\n")
                lines.append(f"- PDF 产物: `{data['filename']}`\n\n")
            lines.append(f"{data['description']}\n\n")
            if data.get("tikz_filename"):
                tikz_path = assets_dir / data["tikz_filename"]
                tikz_path.write_text(_build_tikz_stub(fig_name, data), encoding="utf-8")
                paths[f"{fig_name}_tikz"] = tikz_path
                if runtime.auto_compile_figures():
                    fig_steps.append(BuildStep(id=fig_name, root=data["tikz_filename"], passes=1))
                    step_to_fig[fig_name] = fig_name
                else:
                    figure_compile_status[fig_name] = {
                        "ok": False,
                        "detail": "AUTO_COMPILE_FIGURES=false",
                    }
        # 图片为 standalone tex（不依赖 CPHOS 模板），整批作为一个多步骤作业编译。
        if fig_steps:
            fig_results = _get_compiler().compile_many(assets_dir, fig_steps)
            for fig_name, result in fig_results.items():
                figure_compile_status[fig_name] = {"ok": result.ok, "detail": result.log_tail}
                pdf_path = assets_dir / fig_desc[fig_name]["filename"]
                if result.ok and pdf_path.exists():
                    paths[f"{fig_name}_pdf"] = pdf_path
                    available_figure_pdfs.add(fig_desc[fig_name]["filename"])
        p = assets_dir / "README.md"
        p.write_text("".join(lines), encoding="utf-8")
        paths["figure_descriptions"] = p
        logger.info(f"[output] 导出图片需求: {p}")

    if final_state.get("final_latex"):
        p = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['final_latex']}"
        final_latex = _rewrite_figure_asset_paths(
            final_state["final_latex"],
            task_id,
            fig_desc,
            available_figure_pdfs,
        )
        p.write_text(final_latex, encoding="utf-8")
        paths["final_latex"] = p
        logger.info(f"[output] 导出 LaTeX: {p.name}")

    if runtime.auto_compile_latex() and paths.get("final_latex"):
        latex_compile_status, final_pdf = _compile_final_document(
            task_id, output_dir, available_figure_pdfs
        )
        if final_pdf is not None:
            paths["final_pdf"] = final_pdf
    else:
        latex_compile_status.update({
            "ok": False,
            "detail": "AUTO_COMPILE_LATEX=false",
            "template_dir": CPHOS_TEMPLATE_DIR,
        })

    log_data = {
        "task_id": task_id,
        "topic": final_state.get("topic", ""),
        "difficulty": final_state.get("difficulty", ""),
        "mode": final_state.get("mode", "topic_generation"),
        "source_material": final_state.get("source_material", "")[:200],
        "total_score": final_state.get("total_score", 0),
        "arbiter_decision": final_state.get("arbiter_decision", ""),
        "arbiter_reason": final_state.get("arbiter_reason", ""),
        "error_category": final_state.get("error_category", ""),
        "arbiter_feedback": final_state.get("arbiter_feedback", ""),
        "retry_count": final_state.get("retry_count", 0),
        "problem_retry_count": final_state.get("problem_retry_count", 0),
        "solution_retry_count": final_state.get("solution_retry_count", 0),
        "math_review": final_state.get("math_review", ""),
        "physics_review": final_state.get("physics_review", ""),
        "structure_review": final_state.get("structure_review", ""),
        "quality_review": final_state.get("quality_review", ""),
        "template_report": final_state.get("template_report", ""),
        "block_formula_count": len(final_state.get("formula_dict", {})),
        "inline_formula_count": len(final_state.get("inline_dict", {})),
        "figure_count": len(fig_desc),
        "figure_compile_status": figure_compile_status,
        "latex_compile_status": latex_compile_status,
        "api_budget_start": final_state.get("api_budget_start", {}),
        "api_budget_end": final_state.get("api_budget_end", {}),
        "api_cost_usd": final_state.get("api_cost_usd"),
        "has_final_output": bool(final_state.get("final_latex")),
    }
    p = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['log']}"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    paths["log"] = p

    # ===== 仲裁报告 =====
    decision = final_state.get("arbiter_decision", "N/A")
    decision_label = {
        "PASS": "✅ 通过",
        "PASS_WITH_EDITS": "⚠️ 有条件通过（仅存在用语规范问题，需人工修订）",
        "RETRY_PROBLEM": "🔄 重试未通过（题干问题）",
        "RETRY_SOLUTION": "🔄 重试未通过（解答问题）",
        "ABORT": "❌ 废弃",
    }.get(decision, decision)
    report_lines = [
        f"# 仲裁报告\n\n",
        f"## 基本信息\n\n",
        f"| 项目 | 内容 |\n|---|---|\n",
        f"| 任务 ID | {task_id} |\n",
        f"| 主题 | {final_state.get('topic', '')} |\n",
        f"| 难度 | {final_state.get('difficulty', '')} |\n",
        f"| 总分 | {final_state.get('total_score', 0)} |\n\n",
        f"## API 费用\n\n",
        f"- **Provider**: {final_state.get('api_budget_end', {}).get('provider', 'openrouter')}\n",
        f"- **本题消耗估计**: {final_state.get('api_cost_usd') if final_state.get('api_cost_usd') is not None else '未取得'} USD\n",
        f"- **开始额度快照**: `{json.dumps(final_state.get('api_budget_start', {}), ensure_ascii=False)}`\n",
        f"- **结束额度快照**: `{json.dumps(final_state.get('api_budget_end', {}), ensure_ascii=False)}`\n\n",
        f"## 编译状态\n\n",
        f"- **最终 LaTeX 编译**: {latex_compile_status}\n",
        f"- **图片 PDF 编译**: {figure_compile_status}\n\n",
        f"## 仲裁结果\n\n",
        f"- **裁决**: {decision_label}\n",
        f"- **错误类别**: {final_state.get('error_category', 'N/A')}\n",
        f"- **理由**: {final_state.get('arbiter_reason', '')}\n",
        f"- **重试次数**: {final_state.get('retry_count', 0)} "
        f"(命题 {final_state.get('problem_retry_count', 0)} / 解题 {final_state.get('solution_retry_count', 0)})\n\n",
        f"## 数学审核意见\n\n",
        f"{final_state.get('math_review', '无')}\n\n",
        f"## 物理审核意见\n\n",
        f"{final_state.get('physics_review', '无')}\n\n",
        f"## 结构审核意见\n\n",
        f"{final_state.get('structure_review', '无')}\n\n",
        f"## 质量审核意见\n\n",
        f"{final_state.get('quality_review', '无')}\n\n",
        f"## 仲裁反馈\n\n",
        f"{final_state.get('arbiter_feedback', '无')}\n",
    ]
    p = output_dir / f"{task_id}{ARTIFACT_SUFFIXES['report']}"
    p.write_text("".join(report_lines), encoding="utf-8")
    paths["report"] = p
    logger.info(f"[output] 导出仲裁报告: {p.name}")

    return paths
