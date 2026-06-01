"""
API 请求 / 响应数据模型（pydantic）。

这些模型既用于请求校验与响应序列化，也是 FastAPI 自动生成 OpenAPI 文档的
数据源——字段的 ``description`` 与 ``examples`` 会直接出现在 ``/docs`` 与
导出的 ``docs/api`` 文档中。
"""
from __future__ import annotations

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

from spec.task import (
    DEFAULT_DIFFICULTY, DEFAULT_TOTAL_SCORE, MIN_TOTAL_SCORE, MAX_TOTAL_SCORE,
)

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """分页响应包装：``items`` 为当前页数据，``total`` 为满足条件的总条数。"""

    items: list[T] = Field(default_factory=list, description="当前页数据。")
    total: int = Field(description="满足过滤条件的总条数（用于分页器）。")
    limit: int = Field(description="本次请求的分页大小。")
    offset: int = Field(description="本次请求的偏移量。")


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
        default=DEFAULT_DIFFICULTY,
        description="难度等级描述。",
        examples=[DEFAULT_DIFFICULTY, "省级竞赛"],
    )
    total_score: int = Field(
        default=DEFAULT_TOTAL_SCORE, ge=MIN_TOTAL_SCORE, le=MAX_TOTAL_SCORE,
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


class TaskCancelAccepted(BaseModel):
    """取消请求的受理响应。"""

    task_id: str
    status: Literal["aborting", "aborted"] = Field(
        description="aborting=正在停止（运行中任务，将在阶段边界终止）；"
                    "aborted=已立即终止（排队中尚未启动的任务）。"
    )


class ArtifactInfo(BaseModel):
    """单个产物文件的元数据。"""

    name: str = Field(description="产物逻辑名（如 final_latex / draft / log / report）。")
    filename: str = Field(description="磁盘文件名。")
    size: int = Field(description="文件字节数。")


class TaskStatus(BaseModel):
    """任务状态与摘要。"""

    task_id: str
    user_id: str
    status: Literal["queued", "running", "aborting", "done", "error", "aborted", "interrupted"] = Field(
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
    latex_compile_status: dict[str, Any] = Field(
        default_factory=dict,
        description="最终文档编译状态：{ok, detail, template_dir}；未编译时为空。",
    )
    figure_compile_status: dict[str, Any] = Field(
        default_factory=dict,
        description="各图片编译状态：{图标识: {ok, detail}}；未编译时为空。",
    )
    final_pdf_available: bool = Field(
        default=False, description="是否已生成可下载 / 预览的最终 PDF（final_pdf 产物存在）。",
    )


class TaskCompileResult(BaseModel):
    """按需编译的结果。"""

    task_id: str
    latex_compile_status: dict[str, Any] = Field(
        default_factory=dict, description="最终文档编译状态：{ok, detail, template_dir}。",
    )
    figure_compile_status: dict[str, Any] = Field(
        default_factory=dict, description="各图片编译状态：{图标识: {ok, detail}}。",
    )
    final_pdf_available: bool = Field(
        default=False, description="编译后是否产出可下载 / 预览的最终 PDF。",
    )


class ProgressEvent(BaseModel):
    """单条阶段进度事件。"""

    seq: int = Field(description="任务内单调递增的事件序号。")
    phase: str = Field(description="阶段名（Phase.name，如 REVIEWING）。")
    phase_label: str = Field(default="", description="阶段的中文显示标签。")
    occurrence_id: str = Field(
        default="",
        description="同一阶段同一次执行的稳定标识（running/completed 共享），形如 "
                    "'REVIEWING#2'，供前端无序配对，无需依赖事件顺序。",
    )
    round: int = Field(
        default=1,
        description="重试轮次（从 1 起），由状态机总重试计数推导，便于前端按轮分组。",
    )
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

class MeResponse(BaseModel):
    """当前调用者的身份与角色（任意有效 token 均可访问）。"""

    user_id: str = Field(description="当前 token 所属用户 ID。")
    role: Literal["admin", "user"] = Field(description="当前 token 角色。")
    label: str = Field(default="", description="用户备注名。")
    token_id: str = Field(description="当前会话所用 token 的 ID。")


class CreateUserRequest(BaseModel):
    """创建用户请求。"""

    label: str = Field(default="", description="用户备注名（如团队 / 用途）。")


class UpdateUserRequest(BaseModel):
    """更新用户请求。"""

    label: str = Field(description="新的用户备注名。")


class UserInfo(BaseModel):
    """用户记录。"""

    user_id: str
    label: str = ""
    created_at: str


class UserDetail(UserInfo):
    """用户详情（含 token 数与任务数统计）。"""

    token_count: int = Field(default=0, description="该用户未吊销的 token 数量。")
    task_count: int = Field(default=0, description="该用户的任务总数。")


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


class ErrorResponse(BaseModel):
    """统一错误响应体。

    所有显式抛出的业务错误（``HTTPException`` / :class:`api.errors.ApiError`）均以
    此结构返回，便于前端按 ``code`` 做稳定的分支处理；``detail`` 为面向人类的提示。
    请求体校验错误（FastAPI 422）保留框架默认的结构化 ``{detail: [...]}`` 形态。
    """

    code: str = Field(
        description="稳定的机器可读错误码（如 not_found / forbidden / task_not_terminal）。",
        examples=["task_not_terminal"],
    )
    detail: str = Field(description="面向人类的错误说明。")


class VersionInfo(BaseModel):
    """服务版本与许可证信息。"""

    name: str = Field(description="应用包名。")
    version: str = Field(description="语义化版本号。")
    license: str = Field(description="SPDX 许可证标识。")


class ComponentHealth(BaseModel):
    """单个组件的健康状态。"""

    status: Literal["ok", "error"] = Field(description="组件健康状态。")
    detail: str = Field(default="", description="异常时的简要说明（健康时为空）。")


class HealthStatus(BaseModel):
    """服务整体与组件级健康信息。"""

    status: Literal["ok", "degraded"] = Field(
        description="整体状态：所有组件正常为 ok，任一组件异常为 degraded。"
    )
    components: dict[str, ComponentHealth] = Field(
        default_factory=dict,
        description="组件级健康明细（如 db / worker）。",
    )


class AdminStats(BaseModel):
    """管理端概览统计。"""

    task_total: int = Field(description="任务总数。")
    task_status_counts: dict[str, int] = Field(
        default_factory=dict, description="按生命周期状态分组的任务计数。"
    )
    user_count: int = Field(description="用户总数。")
    active_token_count: int = Field(description="未吊销的 token 数量。")
    token_usage_total: int = Field(description="历史任务累计 total_tokens 用量。")
    api_cost_usd_total: float = Field(description="历史任务累计 API 费用（USD）。")


# ============ LLM 设置（服务商 / 模型配置 / Agent 绑定 / 应用设置） ============

class ProviderCreate(BaseModel):
    """创建 LLM 服务商凭据。"""

    name: str = Field(description="服务商配置名称（唯一）。", examples=["default"])
    kind: str = Field(
        description="服务商类型，需匹配已注册 client provider。",
        examples=["openrouter", "openai_compatible"],
    )
    api_key: str = Field(default="", description="API Key（入库存储，响应中掩码）。")
    base_url: str = Field(
        default="", description="API Base URL（openai_compatible 必填；openrouter 可留空）。"
    )
    proxy: str = Field(
        default="", description="HTTP(S) 代理地址，如 http://mihomo:7890。留空表示直连。"
    )
    timeout: int = Field(default=600, ge=1, description="请求超时（秒）。")
    max_retries: int = Field(default=3, ge=0, description="SDK 自动重试次数。")


class ProviderUpdate(BaseModel):
    """更新 LLM 服务商凭据（仅传需修改字段）。"""

    name: Optional[str] = None
    kind: Optional[str] = None
    api_key: Optional[str] = Field(default=None, description="新 API Key；留空表示不修改。")
    base_url: Optional[str] = None
    proxy: Optional[str] = Field(default=None, description="HTTP(S) 代理地址；留空表示不修改。")
    timeout: Optional[int] = Field(default=None, ge=1)
    max_retries: Optional[int] = Field(default=None, ge=0)


class ProviderInfo(BaseModel):
    """服务商记录（api_key 已脱敏）。"""

    id: str
    name: str
    kind: str
    base_url: str = ""
    proxy: str = ""
    timeout: int = 600
    max_retries: int = 3
    api_key_set: bool = Field(description="是否已配置 API Key。")
    api_key_masked: str = Field(default="", description="脱敏后的 Key 末尾提示。")
    created_at: str = ""
    updated_at: str = ""


class ModelConfigCreate(BaseModel):
    """创建模型配置。"""

    name: str = Field(description="模型配置名称（唯一）。", examples=["big-default"])
    provider_id: str = Field(description="引用的服务商 ID。")
    model: str = Field(default="", description="模型标识（如 google/gemini-2.5-pro）。")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="采样温度。")
    max_tokens: int = Field(default=4096, ge=1, description="单次生成最大 token 数。")
    streaming: bool = Field(default=False, description="是否使用流式响应。")


class ModelConfigUpdate(BaseModel):
    """更新模型配置（仅传需修改字段）。"""

    name: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    streaming: Optional[bool] = None


class ModelConfigInfo(BaseModel):
    """模型配置记录。"""

    id: str
    name: str
    provider_id: str
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 4096
    streaming: bool = False
    created_at: str = ""
    updated_at: str = ""


class AgentBindingInfo(BaseModel):
    """Agent 角色到模型配置的绑定。"""

    role: str = Field(description="Agent 角色名。")
    model_config_id: Optional[str] = Field(
        default=None, description="绑定的模型配置 ID；未绑定为 null。"
    )
    updated_at: Optional[str] = None


class AgentBindingUpdate(BaseModel):
    """更新 Agent 绑定。"""

    model_config_id: str = Field(description="要绑定的模型配置 ID。")


class AppSettingsUpdate(BaseModel):
    """更新运行期应用设置（仅传需修改项）。"""

    max_retry_count: Optional[int] = Field(
        default=None, ge=0, description="单阶段最大重试次数。"
    )
    source_material_max_chars: Optional[int] = Field(
        default=None, ge=0, description="源材料进入上下文的最大字符数。"
    )
    auto_compile_figures: Optional[bool] = Field(
        default=None, description="是否自动编译图片 TikZ。"
    )
    auto_compile_latex: Optional[bool] = Field(
        default=None, description="是否自动编译最终 LaTeX。"
    )
    latex_compile_timeout: Optional[int] = Field(
        default=None, gt=0, description="单次 LaTeX / TikZ 编译子进程超时（秒）。"
    )
    latex_compiler_backend: Optional[Literal["local", "remote"]] = Field(
        default=None, description="LaTeX 编译后端：local（本机 subprocess）或 remote（独立编译服务）。"
    )
    latex_service_base_url: Optional[str] = Field(
        default=None, description="远程编译服务根 URL（backend=remote 时必填）。"
    )
    latex_service_api_key: Optional[str] = Field(
        default=None, description="远程编译服务 API Key（经 Authorization: Bearer 发送）。"
    )
    latex_service_poll_interval: Optional[float] = Field(
        default=None, gt=0, description="远程编译作业状态轮询间隔（秒）。"
    )
    latex_service_max_wait: Optional[float] = Field(
        default=None, gt=0, description="远程编译作业等待终态的客户端总截止（秒）。"
    )
    sse_poll_interval: Optional[float] = Field(
        default=None, gt=0, description="SSE 进度推送轮询间隔（秒）。"
    )
    sse_max_duration: Optional[float] = Field(
        default=None, gt=0, description="SSE 连接最长存活时间（秒）。"
    )


class AppSettingsInfo(BaseModel):
    """运行期应用设置当前值。"""

    max_retry_count: int = 3
    source_material_max_chars: int = 60000
    auto_compile_figures: bool = True
    auto_compile_latex: bool = False
    latex_compile_timeout: int = 180
    latex_compiler_backend: str = "local"
    latex_service_base_url: str = ""
    latex_service_api_key_set: bool = False
    latex_service_api_key_masked: str = ""
    latex_service_poll_interval: float = 2.0
    latex_service_max_wait: float = 1200.0
    sse_poll_interval: float = 1.0
    sse_max_duration: float = 1800.0


class ProviderKindsResponse(BaseModel):
    """已注册的服务商类型（provider kind）清单。"""

    kinds: list[str] = Field(
        description="服务商注册中心已注册的 kind（按字典序）。",
        examples=[["openai_compatible", "openrouter"]],
    )


class AppSettingSpec(BaseModel):
    """单个运行期应用设置项的元信息（供管理端表单渲染）。"""

    key: str = Field(description="设置项键名。")
    type: str = Field(description="值类型：int / float / bool / str。")
    min: Optional[float] = Field(default=None, description="允许的最小值（含）。")
    exclusiveMin: Optional[float] = Field(
        default=None, description="允许的最小值（不含）。"
    )


class LLMOptions(BaseModel):
    """LLM / 应用设置的聚合元数据，驱动管理端表单按后端描述渲染。"""

    provider_kinds: list[str] = Field(description="已注册服务商类型清单。")
    agent_roles: list[str] = Field(description="权威 Agent 角色清单。")
    app_settings: list[AppSettingSpec] = Field(
        description="运行期应用设置项及其类型 / 约束。"
    )
