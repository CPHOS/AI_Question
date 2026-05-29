"""
API 请求 / 响应数据模型（pydantic）。

这些模型既用于请求校验与响应序列化，也是 FastAPI 自动生成 OpenAPI 文档的
数据源——字段的 ``description`` 与 ``examples`` 会直接出现在 ``/docs`` 与
导出的 ``docs/api`` 文档中。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============ 任务 ============

class GenerateRequest(BaseModel):
    """提交生成任务的请求体。"""

    topic: str = Field(
        default="",
        description="物理主题；主题生成模式必填，改编类模式可留空（由源材料推断）。",
        examples=["刚体力学与角动量守恒"],
    )
    source_material: str = Field(
        default="",
        description="源材料文本：文献摘要、原题内容或思路描述（改编类模式使用）。",
    )
    difficulty: str = Field(
        default="国家集训队",
        description="难度等级描述。",
        examples=["国家集训队", "省级竞赛"],
    )
    total_score: int = Field(
        default=40, ge=20, le=80,
        description="题目总分（20-80，CPhO 决赛单题主流为 40）。",
    )
    mode: Optional[Literal[
        "topic_generation", "literature_adaptation",
        "idea_expansion", "problem_enrichment",
    ]] = Field(
        default=None,
        description="命题模式；留空则按是否提供 source_material 自动推断。",
    )


class TaskCreated(BaseModel):
    """任务提交后的即时响应。"""

    task_id: str = Field(description="任务标识，用于后续轮询与下载。")
    status: str = Field(description="任务初始状态。", examples=["queued"])


class ArtifactInfo(BaseModel):
    """单个产物文件的元数据。"""

    name: str = Field(description="产物逻辑名（如 final_latex / draft / log / report）。")
    filename: str = Field(description="磁盘文件名。")
    size: int = Field(description="文件字节数。")


class TaskStatus(BaseModel):
    """任务状态与摘要。"""

    task_id: str
    user_id: str
    status: Literal["queued", "running", "done", "error", "aborted", "interrupted"] = Field(
        description="任务生命周期状态。"
    )
    phase: str = Field(
        default="",
        description="当前 / 最近所处的工作流阶段名（如 REVIEWING / FORMATTING）。",
    )
    mode: str = ""
    topic: str = ""
    total_score: int = 0
    created_at: str = ""
    finished_at: Optional[str] = None
    error: Optional[str] = None
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="完成后的摘要：仲裁裁决、重试计数、token 用量、API 费用、产物清单等。",
    )


class TaskListItem(BaseModel):
    """任务列表项（精简）。"""

    task_id: str
    user_id: str
    status: str
    mode: str = ""
    topic: str = ""
    total_score: int = 0
    created_at: str = ""
    finished_at: Optional[str] = None
    error: Optional[str] = None


class TaskResult(BaseModel):
    """任务关键文本结果（内联返回）。"""

    task_id: str
    status: str
    final_latex: str = Field(default="", description="最终可编译的 CPHOS LaTeX 文本。")
    report: str = Field(default="", description="仲裁报告 Markdown 文本。")
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


class ProgressEvent(BaseModel):
    """单条阶段进度事件。"""

    seq: int = Field(description="任务内单调递增的事件序号。")
    phase: str = Field(description="阶段名（Phase.name，如 REVIEWING）。")
    phase_label: str = Field(default="", description="阶段的中文显示标签。")
    status: Literal["running", "completed"] = Field(
        description="running=进入该阶段；completed=该阶段产出就绪。"
    )
    output: Optional[dict[str, Any]] = Field(
        default=None,
        description="completed 事件携带的结构化阶段产出快照（已按阶段类型格式化，"
                    "非模型原始输出）。",
    )
    created_at: str = Field(description="事件时间戳。")


class TaskProgress(BaseModel):
    """任务的节点级进度（阶段事件时间线）。"""

    task_id: str
    status: str = Field(description="任务生命周期状态。")
    phase: str = Field(default="", description="当前 / 最近所处的阶段名。")
    events: list[ProgressEvent] = Field(
        default_factory=list, description="按序排列的阶段事件时间线。"
    )


# ============ 管理 ============

class CreateUserRequest(BaseModel):
    """创建用户请求。"""

    label: str = Field(default="", description="用户备注名（如团队 / 用途）。")


class UserInfo(BaseModel):
    """用户记录。"""

    user_id: str
    label: str = ""
    created_at: str


class CreateTokenRequest(BaseModel):
    """为用户签发 token 的请求。"""

    user_id: str = Field(description="目标用户 ID。")
    role: Literal["admin", "user"] = Field(default="user", description="token 角色。")
    label: str = Field(default="", description="token 备注（如分发对象）。")


class TokenCreated(BaseModel):
    """新签发 token 的响应（含一次性明文）。"""

    id: str
    token: str = Field(description="token 明文，仅此一次返回，请立即安全保存并分发。")
    user_id: str
    role: str
    label: str = ""
    created_at: str


class TokenInfo(BaseModel):
    """token 元数据（不含明文 / 哈希）。"""

    id: str
    user_id: str
    role: str
    label: str = ""
    created_at: str
    revoked_at: Optional[str] = None


class MessageResponse(BaseModel):
    """通用消息响应。"""

    detail: str
