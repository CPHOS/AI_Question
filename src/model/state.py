"""
全局状态定义。
使用 TypedDict 确保类型安全。

关于并行节点写冲突说明：
  math_verifier 仅写入 math_review，physics_verifier 仅写入 physics_review。
  两个并行节点写入不同的 state key，工作流引擎会自动合并，无需定义 reducer。
"""
from typing import TypedDict


class AgentState(TypedDict):
    topic: str                 # 输入：物理主题
    difficulty: str            # 输入：难度等级
    total_score: int           # 输入：题目总分（20-80，默认 40）
    title: str                 # 命题 Agent 自拟的题目标题
    draft_content: str         # 命题 Agent 生成的完整题目与解答
    math_review: str           # 数学验算 Agent 的审核意见
    physics_review: str        # 物理验算 Agent 的审核意见
    arbiter_decision: str      # 仲裁结果: "PASS" | "RETRY" | "ABORT"
    arbiter_feedback: str      # 仲裁给出的修改建议
    arbiter_reason: str        # 仲裁做出判决的理由摘要
    error_category: str        # 错误类别: "none" | "style" | "fatal"
    retry_count: int           # 当前重试次数（0 = 首次生成）
    formula_dict: dict         # Block 公式字典: {"{{BLOCK_MATH_1}}": {"label": "...", "content": "...", "score": "..."}}
    inline_dict: dict          # Inline 公式字典: {"{{INLINE_MATH_1}}": "..."}
    figure_dict: dict          # Figure 字典: {"{{FIGURE_1}}": {"label": "...", "caption": "...", "description": "..."}}
    tagged_text: str           # 双重隔离后的占位符文本
    formatted_text: str        # 格式化 Agent 排版后的文本（仍含占位符）
    final_latex: str           # 最终 LaTeX（公式已回填）
    figure_descriptions: dict  # 图片绘制需求: {"fig_1": {"filename": "...", "caption": "...", "description": "..."}}
