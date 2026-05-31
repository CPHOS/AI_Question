"""
用户任务路由：提交、轮询、历史、产物下载、删除。

访问控制：普通用户只能操作自己的任务；管理员（全权限）可操作任意用户的任务
与产物。
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile

from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status
from fastapi.responses import FileResponse, StreamingResponse

from api import jobs, store
from api import progress
from api.auth import Identity, require_user
from api.errors import (
    ApiError, CODE_FORBIDDEN, CODE_NOT_FOUND, CODE_UNPROCESSABLE,
    CODE_TASK_NOT_FOUND, CODE_TASK_NOT_TERMINAL, CODE_TASK_ALREADY_TERMINAL,
    CODE_TASK_NOT_RETRYABLE, CODE_FINAL_LATEX_MISSING, CODE_COMPILE_IN_PROGRESS,
)
from api.schemas import (
    GenerateRequest, TaskCreated, TaskStatus, TaskListItem, TaskResult,
    ArtifactInfo, MessageResponse, ProgressEvent, TaskProgress, Page,
    MeResponse, TaskCancelAccepted, TaskCompileResult,
)
from config import runtime
from spec.task import DEFAULT_DIFFICULTY, DEFAULT_TOTAL_SCORE, MIN_TOTAL_SCORE, MAX_TOTAL_SCORE
from app.outputs import ASSETS_DIR_SUFFIX

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 身份端点单独挂在 /api 下（非任务子资源）。
me_router = APIRouter(prefix="/api", tags=["system"])

# 产物逻辑名 / 文件后缀 → 下载时的 MIME 类型。
_DEFAULT_MEDIA_TYPE = "application/octet-stream"
_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".tex": "application/x-tex; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# 任务终态集合（不可再被取消）。
_TERMINAL_STATUSES = {"done", "error", "aborted", "interrupted"}

# 可被「重跑」的终态：仅失败 / 中止类，``done`` 不在其列。
_RETRYABLE_STATUSES = {"error", "aborted", "interrupted"}


def _media_type_for(filename: str) -> str:
    """按文件后缀返回下载 MIME 类型。"""
    lower = filename.lower()
    for suffix, mime in _MEDIA_TYPES.items():
        if lower.endswith(suffix):
            return mime
    return _DEFAULT_MEDIA_TYPE


@me_router.get("/me", response_model=MeResponse, summary="返回当前身份与角色")
def get_me(identity: Identity = Depends(require_user)) -> MeResponse:
    """返回当前 token 对应的用户 ID、角色与备注名，可用于登录后路由与 token 校验。"""
    user = store.get_user(identity.user_id)
    label = user["label"] if user else ""
    return MeResponse(
        user_id=identity.user_id,
        role=identity.role,
        label=label,
        token_id=identity.token_id,
    )


def _load_task_or_404(task_id: str, identity: Identity) -> dict:
    """加载任务并校验归属（owner 或 admin）。"""
    task = store.get_task(task_id)
    if task is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, CODE_TASK_NOT_FOUND, "任务不存在")
    if not identity.is_admin and task["user_id"] != identity.user_id:
        raise ApiError(status.HTTP_403_FORBIDDEN, CODE_FORBIDDEN, "无权访问该任务")
    return task


@router.post("", response_model=TaskCreated, status_code=status.HTTP_202_ACCEPTED,
             summary="提交生成任务")
def create_task(req: GenerateRequest, identity: Identity = Depends(require_user)) -> TaskCreated:
    """提交一个异步生成任务，立即返回 ``task_id``；通过轮询获取进度与结果。"""
    if not req.topic and not req.source_material:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            CODE_UNPROCESSABLE,
            "topic 与 source_material 不能同时为空",
        )
    task_id = jobs.submit_task(
        identity.user_id,
        topic=req.topic,
        source_material=req.source_material[:runtime.source_material_max_chars()],
        difficulty=req.difficulty,
        total_score=req.total_score,
        mode=req.mode,
    )
    return TaskCreated(task_id=task_id, status="queued")


@router.post("/upload", response_model=TaskCreated, status_code=status.HTTP_202_ACCEPTED,
             summary="上传源材料并提交改编任务")
async def create_task_upload(
    file: UploadFile = File(..., description="源材料文本文件"),
    difficulty: str = Form(DEFAULT_DIFFICULTY),
    total_score: int = Form(DEFAULT_TOTAL_SCORE, ge=MIN_TOTAL_SCORE, le=MAX_TOTAL_SCORE),
    mode: str | None = Form(None),
    topic: str = Form(""),
    identity: Identity = Depends(require_user),
) -> TaskCreated:
    """上传源材料文件并提交改编任务。"""
    raw = await file.read()
    try:
        # utf-8-sig：自动剥离可能的 BOM（无 BOM 时等价于普通 UTF-8 解码）。
        source_material = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            CODE_UNPROCESSABLE,
            "仅支持 UTF-8 文本源材料",
        )
    if not topic and file.filename:
        topic = file.filename.rsplit(".", 1)[0]
    task_id = jobs.submit_task(
        identity.user_id,
        topic=topic,
        source_material=source_material[:runtime.source_material_max_chars()],
        difficulty=difficulty,
        total_score=total_score,
        mode=mode,
    )
    return TaskCreated(task_id=task_id, status="queued")


def _parse_statuses(status_param: str | None) -> list[str] | None:
    """把逗号分隔的 status 查询参数解析为列表（去空白 / 去空项）。"""
    if not status_param:
        return None
    values = [s.strip() for s in status_param.split(",") if s.strip()]
    return values or None


@router.get("", response_model=Page[TaskListItem], summary="列出当前用户的任务历史")
def list_my_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="按状态过滤，可逗号分隔（如 running,error）。"),
    mode: str | None = Query(None, description="按命题模式过滤。"),
    q: str | None = Query(None, description="按 topic 模糊搜索。"),
    order: str = Query("DESC", description="按创建时间排序：ASC / DESC。"),
    identity: Identity = Depends(require_user),
) -> Page[TaskListItem]:
    """分页列出调用者自己的历史任务，支持状态 / 模式 / 主题过滤与排序。"""
    statuses = _parse_statuses(status)
    rows = store.list_tasks(
        user_id=identity.user_id, limit=limit, offset=offset,
        statuses=statuses, mode=mode, q=q, order=order,
    )
    total = store.count_tasks(user_id=identity.user_id, statuses=statuses, mode=mode, q=q)
    return Page(items=[TaskListItem(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskStatus, summary="查询任务状态与摘要")
def get_task_status(task_id: str, identity: Identity = Depends(require_user)) -> TaskStatus:
    """返回任务状态及完成后的摘要（裁决、token 用量、产物清单等）。"""
    task = _load_task_or_404(task_id, identity)
    return TaskStatus(**task)


@router.get("/{task_id}/progress", response_model=TaskProgress,
            summary="查询任务的节点级进度")
def get_task_progress(task_id: str, identity: Identity = Depends(require_user)) -> TaskProgress:
    """返回任务的阶段事件时间线，含每个已完成阶段的结构化产出快照。

    可在轮询任务期间调用，用于展示当前所处节点与各阶段的格式化结果
    （非模型原始输出）。
    """
    task = _load_task_or_404(task_id, identity)
    events = store.list_events(task_id)
    return TaskProgress(
        task_id=task_id,
        status=task["status"],
        phase=task.get("phase", "") or "",
        events=[
            ProgressEvent(
                seq=e["seq"],
                phase=e["phase"],
                phase_label=progress.phase_label(e["phase"]),
                occurrence_id=e.get("occurrence_id", ""),
                round=e.get("round", 1),
                status=e["status"],
                output=e["output"],
                created_at=e["created_at"],
            )
            for e in events
        ],
    )


@router.get("/{task_id}/artifacts", response_model=list[ArtifactInfo],
            summary="列出任务产物")
def list_task_artifacts(
    task_id: str, identity: Identity = Depends(require_user),
) -> list[ArtifactInfo]:
    """列出任务产物文件及其大小。"""
    task = _load_task_or_404(task_id, identity)
    items = jobs.list_artifacts(task["user_id"], task_id)
    return [ArtifactInfo(**i) for i in items]


@router.get(
    "/{task_id}/artifacts/archive",
    summary="打包下载全部产物（zip）",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "全部产物的 zip 压缩包，附 Content-Disposition 文件名。",
            "content": {"application/zip": {}},
        }
    },
)
def download_artifacts_archive(
    task_id: str, identity: Identity = Depends(require_user),
) -> StreamingResponse:
    """把任务的全部产物打包为单个 zip 流式返回，便于「一键下载全部」。

    压缩包内文件名沿用产物的磁盘名（含 ``{task_id}_assets/`` 子目录结构）。
    """
    task = _load_task_or_404(task_id, identity)
    owner = task["user_id"]
    items = jobs.list_artifacts(owner, task_id)
    if not items:
        raise ApiError(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, "该任务暂无可下载产物")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            path = jobs.resolve_artifact(owner, task_id, item["name"])
            if path is not None and path.exists():
                archive.write(path, arcname=item["filename"])
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{task_id}.zip"'},
    )


@router.get(
    "/{task_id}/artifacts/{name:path}",
    summary="下载单个产物",
    response_class=FileResponse,
    responses={
        200: {
            "description": "产物二进制内容（MIME 按类型设置，并附 Content-Disposition 文件名）。",
            "content": {
                "application/pdf": {},
                "application/x-tex": {},
                "text/markdown": {},
                "application/octet-stream": {},
            },
        }
    },
)
def download_artifact(
    task_id: str,
    name: str,
    disposition: str = Query(
        "attachment",
        pattern="^(attachment|inline)$",
        description="Content-Disposition 类型：attachment=下载（默认）；inline=浏览器内联预览（如 PDF）。",
    ),
    identity: Identity = Depends(require_user),
) -> FileResponse:
    """下载或内联预览指定产物文件（如 ``final_latex`` / ``log`` / ``assets/fig1.pdf``）。

    响应按文件类型设置 ``Content-Type``（如 ``.pdf → application/pdf``）。默认以
    ``attachment`` 触发下载；传 ``?disposition=inline`` 则以 ``inline`` 返回，便于
    在浏览器 / iframe 中直接预览 PDF。
    """
    task = _load_task_or_404(task_id, identity)
    path = jobs.resolve_artifact(task["user_id"], task_id, name)
    if path is None or not path.exists():
        raise ApiError(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, "产物不存在")
    return FileResponse(
        path,
        filename=path.name,
        media_type=_media_type_for(path.name),
        content_disposition_type=disposition,
    )


@router.get("/{task_id}/result", response_model=TaskResult, summary="内联返回关键文本结果")
def get_task_result(task_id: str, identity: Identity = Depends(require_user)) -> TaskResult:
    """内联返回最终 LaTeX、仲裁报告文本与编译状态，便于直接消费。"""
    task = _load_task_or_404(task_id, identity)
    owner = task["user_id"]
    artifacts = jobs.list_artifacts(owner, task_id)

    def _read(name: str) -> str:
        p = jobs.resolve_artifact(owner, task_id, name)
        return p.read_text(encoding="utf-8") if p and p.exists() else ""

    compile_status = jobs.read_compile_status(owner, task_id)
    return TaskResult(
        task_id=task_id,
        status=task["status"],
        final_latex=_read("final_latex"),
        report=_read("report"),
        artifacts=[ArtifactInfo(**i) for i in artifacts],
        latex_compile_status=compile_status["latex_compile_status"],
        figure_compile_status=compile_status["figure_compile_status"],
        final_pdf_available=any(a["name"] == "final_pdf" for a in artifacts),
    )


@router.post("/{task_id}/compile", response_model=TaskCompileResult, summary="按需编译最终 LaTeX 与图片")
def compile_task(task_id: str, identity: Identity = Depends(require_user)) -> TaskCompileResult:
    """对已生成的 ``final_latex`` 与图片 TikZ 源**重新编译**为 PDF（不调用 LLM）。

    用于生成时 ``AUTO_COMPILE_LATEX=false`` 未产出 PDF、或需刷新 PDF 的场景。
    要求任务已处于终态且存在 ``final_latex`` 产物；同一任务的编译会串行化执行。

    - 运行 / 排队中的任务（非终态）→ 409；
    - 无 ``final_latex`` 产物 → 409；
    - 已有编译在进行中 → 409。
    """
    task = _load_task_or_404(task_id, identity)
    if task["status"] not in _TERMINAL_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            CODE_TASK_NOT_TERMINAL,
            f"任务当前状态 {task['status']} 不可编译（需先到达终态）",
        )
    owner = task["user_id"]
    try:
        result = jobs.recompile_task(owner, task_id)
    except FileNotFoundError as exc:
        raise ApiError(status.HTTP_409_CONFLICT, CODE_FINAL_LATEX_MISSING, str(exc))
    except RuntimeError as exc:
        raise ApiError(status.HTTP_409_CONFLICT, CODE_COMPILE_IN_PROGRESS, str(exc))

    final_pdf_available = any(
        a["name"] == "final_pdf" for a in jobs.list_artifacts(owner, task_id)
    )
    return TaskCompileResult(
        task_id=task_id,
        latex_compile_status=result["latex_compile_status"],
        figure_compile_status=result["figure_compile_status"],
        final_pdf_available=final_pdf_available,
    )


@router.delete("/{task_id}", response_model=MessageResponse, summary="删除任务及其产物")
def delete_task(task_id: str, identity: Identity = Depends(require_user)) -> MessageResponse:
    """删除任务元数据与磁盘产物。运行中的任务不会被中断，仅清理记录。"""
    task = _load_task_or_404(task_id, identity)
    owner = task["user_id"]
    base = jobs.user_output_dir(owner)
    for item in jobs.list_artifacts(owner, task_id):
        p = (base / item["filename"])
        if p.exists() and p.is_file():
            p.unlink()
    assets = base / f"{task_id}{ASSETS_DIR_SUFFIX}"
    if assets.is_dir():
        shutil.rmtree(assets, ignore_errors=True)
    store.delete_task(task_id)
    jobs.discard_compile_lock(task_id)
    return MessageResponse(detail=f"任务 {task_id} 已删除")


@router.post("/{task_id}/cancel", response_model=TaskCancelAccepted,
             status_code=status.HTTP_202_ACCEPTED, summary="中止运行中的任务")
def cancel_task(task_id: str, identity: Identity = Depends(require_user)) -> TaskCancelAccepted:
    """协作式取消任务。

    - 队列中尚未启动的任务会被立即标记为 ``aborted``；
    - 运行中的任务标记为 ``aborting``，工作流在下一个阶段边界停止后落为 ``aborted``。

    已处于终态（``done`` / ``error`` / ``aborted`` / ``interrupted``）的任务返回 409。
    """
    task = _load_task_or_404(task_id, identity)
    if task["status"] in _TERMINAL_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            CODE_TASK_ALREADY_TERMINAL,
            f"任务已处于终态 {task['status']}，无法取消",
        )
    result_status = jobs.cancel_task(task_id)
    return TaskCancelAccepted(task_id=task_id, status=result_status)


@router.post("/{task_id}/retry", response_model=TaskCreated,
             status_code=status.HTTP_202_ACCEPTED, summary="以原输入重跑失败 / 中止的任务")
def retry_task(task_id: str, identity: Identity = Depends(require_user)) -> TaskCreated:
    """基于原任务的输入克隆并重新入队，返回新的 ``task_id``。

    仅 ``error`` / ``aborted`` / ``interrupted`` 终态的任务可重试；其余状态返回 409。
    原任务记录保持不变，重跑生成的是一个全新的独立任务。
    """
    task = _load_task_or_404(task_id, identity)
    if task["status"] not in _RETRYABLE_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            CODE_TASK_NOT_RETRYABLE,
            f"任务当前状态 {task['status']} 不可重试（仅 error / aborted / interrupted 可重试）",
        )
    # 历史任务（迁移前创建）可能未持久化 difficulty，回退到请求模型的默认值。
    default_difficulty = GenerateRequest.model_fields["difficulty"].default
    default_total_score = GenerateRequest.model_fields["total_score"].default
    new_id = jobs.submit_task(
        task["user_id"],
        topic=task.get("topic", ""),
        source_material=(task.get("source_material") or "")[:runtime.source_material_max_chars()],
        difficulty=task.get("difficulty") or default_difficulty,
        total_score=task.get("total_score") or default_total_score,
        mode=task.get("mode") or None,
    )
    return TaskCreated(task_id=new_id, status="queued")


async def _sse_event_stream(task_id: str):
    """SSE 生成器：增量推送阶段事件，任务进入终态后推送状态并结束。"""
    last_seq = 0
    elapsed = 0.0
    # 轮询间隔与最长存活时间（秒），防止连接泄漏；取自可持久化运行期设置。
    poll_interval = runtime.sse_poll_interval()
    max_duration = runtime.sse_max_duration()
    # 首屏立即下发一个 ready 注释，建立连接。
    yield ": connected\n\n"
    while elapsed < max_duration:
        try:
            events = store.list_events(task_id)
        except Exception:  # noqa: BLE001 - 读库异常不应中断流，下一轮重试
            events = []
        for e in events:
            if e["seq"] <= last_seq:
                continue
            last_seq = e["seq"]
            payload = {
                "seq": e["seq"],
                "phase": e["phase"],
                "phase_label": progress.phase_label(e["phase"]),
                "occurrence_id": e.get("occurrence_id", ""),
                "round": e.get("round", 1),
                "status": e["status"],
                "output": e["output"],
                "created_at": e["created_at"],
            }
            yield f"event: phase\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        task = store.get_task(task_id)
        if task is None:
            yield f"event: status\ndata: {json.dumps({'status': 'deleted'})}\n\n"
            return
        if task["status"] in _TERMINAL_STATUSES:
            yield f"event: status\ndata: {json.dumps({'status': task['status']}, ensure_ascii=False)}\n\n"
            return

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


@router.get(
    "/{task_id}/events",
    summary="实时进度推送（SSE）",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Server-Sent Events 流。帧格式：\n\n"
                "- `: connected` —— 建立连接时的注释帧（心跳/就绪标记，无 event）。\n"
                "- `event: phase` —— 阶段事件帧，`data` 为 JSON，结构同 `ProgressEvent` "
                "（字段：seq / phase / phase_label / occurrence_id / round / status / "
                "output / created_at）。\n"
                "- `event: status` —— 终止帧，`data` 为 `{\"status\": <终态>}`（任务进入 "
                "done / error / aborted / interrupted，或被删除时 `deleted`），随后服务端关闭流。"
            ),
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_task_events(
    task_id: str, identity: Identity = Depends(require_user),
) -> StreamingResponse:
    """以 Server-Sent Events 增量推送阶段事件，减少轮询。

    发送两类事件：``phase``（阶段事件，含格式化产出，结构同 ``ProgressEvent``）与
    ``status``（任务进入终态时的最终状态）。连接建立时先下发一个 ``: connected``
    注释帧。客户端在不支持时可回退到轮询 ``/progress``。
    """
    _load_task_or_404(task_id, identity)
    return StreamingResponse(
        _sse_event_stream(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
