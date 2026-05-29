"""
用户任务路由：提交、轮询、历史、产物下载、删除。

访问控制：普通用户只能操作自己的任务；管理员（全权限）可操作任意用户的任务
与产物。
"""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse

from api import jobs, store
from api import progress
from api.auth import Identity, require_user
from api.schemas import (
    GenerateRequest, TaskCreated, TaskStatus, TaskListItem, TaskResult,
    ArtifactInfo, MessageResponse, ProgressEvent, TaskProgress,
)
from config.config import SOURCE_MATERIAL_MAX_CHARS

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _load_task_or_404(task_id: str, identity: Identity) -> dict:
    """加载任务并校验归属（owner 或 admin）。"""
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if not identity.is_admin and task["user_id"] != identity.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该任务")
    return task


@router.post("", response_model=TaskCreated, status_code=status.HTTP_202_ACCEPTED,
             summary="提交生成任务")
def create_task(req: GenerateRequest, identity: Identity = Depends(require_user)) -> TaskCreated:
    """提交一个异步生成任务，立即返回 ``task_id``；通过轮询获取进度与结果。"""
    if not req.topic and not req.source_material:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="topic 与 source_material 不能同时为空",
        )
    task_id = jobs.submit_task(
        identity.user_id,
        topic=req.topic,
        source_material=req.source_material[:SOURCE_MATERIAL_MAX_CHARS],
        difficulty=req.difficulty,
        total_score=req.total_score,
        mode=req.mode,
    )
    return TaskCreated(task_id=task_id, status="queued")


@router.post("/upload", response_model=TaskCreated, status_code=status.HTTP_202_ACCEPTED,
             summary="上传源材料并提交改编任务")
async def create_task_upload(
    file: UploadFile = File(..., description="源材料文本文件"),
    difficulty: str = Form("国家集训队"),
    total_score: int = Form(40),
    mode: str | None = Form(None),
    topic: str = Form(""),
    identity: Identity = Depends(require_user),
) -> TaskCreated:
    """上传源材料文件（对应 CLI ``--adapt``）并提交改编任务。"""
    raw = await file.read()
    try:
        source_material = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅支持 UTF-8 文本源材料",
        )
    if not topic and file.filename:
        topic = file.filename.rsplit(".", 1)[0]
    task_id = jobs.submit_task(
        identity.user_id,
        topic=topic,
        source_material=source_material[:SOURCE_MATERIAL_MAX_CHARS],
        difficulty=difficulty,
        total_score=total_score,
        mode=mode,
    )
    return TaskCreated(task_id=task_id, status="queued")


@router.get("", response_model=list[TaskListItem], summary="列出当前用户的任务历史")
def list_my_tasks(
    limit: int = 50, offset: int = 0, identity: Identity = Depends(require_user),
) -> list[TaskListItem]:
    """分页列出调用者自己的历史任务（按创建时间倒序）。"""
    rows = store.list_tasks(user_id=identity.user_id, limit=limit, offset=offset)
    return [TaskListItem(**r) for r in rows]


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


@router.get("/{task_id}/artifacts/{name:path}", summary="下载单个产物")
def download_artifact(
    task_id: str, name: str, identity: Identity = Depends(require_user),
) -> FileResponse:
    """下载指定产物文件（如 ``final_latex`` / ``log`` / ``assets/fig1.pdf``）。"""
    task = _load_task_or_404(task_id, identity)
    path = jobs.resolve_artifact(task["user_id"], task_id, name)
    if path is None or not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="产物不存在")
    return FileResponse(path, filename=path.name)


@router.get("/{task_id}/result", response_model=TaskResult, summary="内联返回关键文本结果")
def get_task_result(task_id: str, identity: Identity = Depends(require_user)) -> TaskResult:
    """内联返回最终 LaTeX 与仲裁报告文本，便于直接消费。"""
    task = _load_task_or_404(task_id, identity)
    owner = task["user_id"]
    artifacts = jobs.list_artifacts(owner, task_id)

    def _read(name: str) -> str:
        p = jobs.resolve_artifact(owner, task_id, name)
        return p.read_text(encoding="utf-8") if p and p.exists() else ""

    return TaskResult(
        task_id=task_id,
        status=task["status"],
        final_latex=_read("final_latex"),
        report=_read("report"),
        artifacts=[ArtifactInfo(**i) for i in artifacts],
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
    assets = base / f"{task_id}_assets"
    if assets.is_dir():
        shutil.rmtree(assets, ignore_errors=True)
    store.delete_task(task_id)
    return MessageResponse(detail=f"任务 {task_id} 已删除")
