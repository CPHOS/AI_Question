"""
工作流数据模型 — 按阶段拆分的 TypedDict 集合。

设计原则（针对 PR #5 wu 的 review #4：
"全量容器的设计思路不合理，不应当维护一个流转全局的大数据块。
应当在每一环节维护自己的数据块记录分别记录"）：

1. **每个阶段维护自己的数据记录**，作为独立 `TypedDict`：
     - `TaskInput`             — 输入与命题规划阶段的写入产物
     - `GenerationOutput`      — 命题 / 解题 Agent 的写入产物
     - `ReviewOutput`          — 数学 / 物理 / 结构审核的写入产物
     - `ArbitrationOutput`     — 仲裁 Agent 的写入产物（含分阶段重试计数）
     - `LaTeXOutput`           — LaTeX 后处理流水线的写入产物

   每个 Agent 的返回类型应当是它**所拥有阶段**的 TypedDict（或其子集），
   而不是无差别的 `dict` —— 通过类型注解显式标记字段归属，避免跨阶段写冲突。

2. **顶层 `WorkflowData`** 通过 `TypedDict` 多继承把上述阶段记录合并起来，
   只用于：
     - 状态机内部用 `data.update(stage_output)` 把阶段产物合并到流转字典；
     - 规格化器（`spec.normalizer`）一次性给出全部字段的初始空状态；
     - CLI / 输出层（`app.__init__`）从 final state 中读取需要写出的内容。

   它**不再**作为各阶段写入字段的"权威清单"——权威定义在各阶段的 TypedDict 中。

3. 并行节点写冲突说明：
   `math_verifier` 仅写 `math_review`、`physics_verifier` 仅写 `physics_review`、
   `structure_checker` 仅写 `structure_review`。三者属于同一阶段
   (`ReviewOutput`) 的不同字段，并行写不冲突。

4. 选择 `TypedDict` 而非 `pydantic.BaseModel`：
   - 工作流字典在阶段间用 `dict.update()` 合并是高频操作；用对象会引入额外封装；
   - 现有 16 个调用点（agents / latex / engine / spec）均按 `data["k"]` 访问；
   - 与现有 mock-based 测试（`tests/test_state_machine.py`）兼容。
"""
from __future__ import annotations

from typing import TypedDict


# ---------------------------------------------------------------------------
# 阶段 1：输入与命题规划
# ---------------------------------------------------------------------------

class TaskInput(TypedDict, total=False):
    """输入规格化器 + 命题规划 Agent 的写入产物。

    所属阶段：`PLANNING` 之前由 `spec.normalizer` 写入；`planning_notes`
    由 `spec.planner.run_planning` 在 `PLANNING` 阶段补齐。

    `total=False` —— 各字段为"可选"以允许 Agent 仅返回它真正写入的子集
    （例如 planner 只返回 `{"planning_notes": "..."}`）。
    """

    mode: str                    # "topic_generation" | "literature_adaptation" | "idea_expansion" | "problem_enrichment"
    topic: str                   # 物理主题
    source_material: str         # 源材料（文献 / 原题 / 思路；自由命题为空）
    difficulty: str              # 难度等级描述
    total_score: int             # 题目总分（20-80）
    difficulty_profile: dict     # 三维难度目标
    planning_notes: str          # 规划 Agent 输出的命题规划文本


# ---------------------------------------------------------------------------
# 阶段 2：命题 / 解题生成
# ---------------------------------------------------------------------------

class GenerationOutput(TypedDict, total=False):
    """命题 Agent + 解题 Agent 的写入产物。

    所属阶段：`PROBLEM_GENERATING` / `SOLUTION_GENERATING`。
    `draft_content` 由状态机在两阶段完成后由 `problem_text + solution_text`
    拼接，供下游审核 Agent 消费。

    `total=False` —— 命题 / 解题 Agent 各自只写其中部分字段。
    """

    title: str                   # 命题 Agent 自拟的题目标题
    problem_text: str            # 题干 + 小问（命题生成 Agent 产出）
    solution_text: str           # 参考答案 + 评分点（解题生成 Agent 产出）
    draft_content: str           # problem_text + solution_text 合并文本


# ---------------------------------------------------------------------------
# 阶段 3：审核（数学 / 物理 / 结构）
# ---------------------------------------------------------------------------

class ReviewOutput(TypedDict, total=False):
    """三份并行审核 Agent 的写入产物。

    所属阶段：`REVIEWING`。三个字段由不同子 Agent 互斥写入，无锁。

    `total=False` —— 单个子 Agent 只写自己负责的那个字段。
    """

    math_review: str             # 数学检查 Agent 意见
    physics_review: str          # 物理检查 Agent 意见
    structure_review: str        # 结构检查器（纯规则，无 LLM）意见


# ---------------------------------------------------------------------------
# 阶段 4：仲裁 + 分阶段重试计数
# ---------------------------------------------------------------------------

class ArbitrationOutput(TypedDict, total=False):
    """仲裁 Agent 的写入产物。

    所属阶段：`ARBITRATING`。`*_retry_count` 仅由本 Agent 在做出 RETRY_*
    裁决时递增，命题 / 解题 Agent **不修改**这些计数器。

    分阶段重试计数语义：
      - RETRY_PROBLEM   → problem_retry_count += 1
      - RETRY_SOLUTION  → solution_retry_count += 1
      - PASS / ABORT    → 不递增（首轮直接通过不计 retry）
      - retry_count = problem_retry_count + solution_retry_count（展示用）
    """

    arbiter_decision: str        # "PASS" | "RETRY_PROBLEM" | "RETRY_SOLUTION" | "ABORT"
    arbiter_feedback: str        # 仲裁给出的修改建议
    arbiter_reason: str          # 仲裁做出判决的理由摘要
    error_category: str          # "none" | "style" | "fatal"
    retry_count: int             # 总重试次数（= problem + solution）
    problem_retry_count: int     # 命题阶段被要求重新生成的次数
    solution_retry_count: int    # 解题阶段被要求重新生成的次数


# ---------------------------------------------------------------------------
# 阶段 5：LaTeX 后处理
# ---------------------------------------------------------------------------

class LaTeXOutput(TypedDict, total=False):
    """LaTeX 后处理流水线的写入产物。

    所属阶段：`FORMATTING` → `TEMPLATE_FIXING`。
    流转顺序：`isolate` → `format` → `merge` → `fix_template`，
    每步只追加 / 改写自己的字段，不回填其他阶段的字段。

    `total=False` —— 流水线分多步逐步追加字段。
    """

    formula_dict: dict           # Block 公式字典
    inline_dict: dict            # Inline 公式字典
    figure_dict: dict            # Figure 字典
    tagged_text: str             # 占位符文本
    formatted_text: str          # 格式化 Agent 排版后文本（含占位符）
    final_latex: str             # 最终 LaTeX（公式已回填）
    template_report: str         # 模板修正报告
    figure_descriptions: dict    # 图片绘制需求


# ---------------------------------------------------------------------------
# 顶层合并视图
# ---------------------------------------------------------------------------

class WorkflowData(
    TaskInput,
    GenerationOutput,
    ReviewOutput,
    ArbitrationOutput,
    LaTeXOutput,
):
    """所有阶段记录合并后的工作流字典视图。

    实际运行时，状态机以 `dict.update(stage_output)` 的方式把每个阶段
    的 TypedDict 合并进同一个 dict 实例。访问任何阶段字段都允许，
    但**写入应当通过对应阶段的 Agent**，由其返回该阶段的 TypedDict。

    新增字段时：先决定它属于哪个阶段，再加到对应的阶段 TypedDict；
    不要直接往 `WorkflowData` 加字段。
    """
    pass
