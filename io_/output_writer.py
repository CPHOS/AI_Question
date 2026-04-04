"""
输出写入器：将最终结果和中间态写入 output/ 目录。

输出文件:
- {task_id}_final.tex  : 最终 LaTeX 成品
- {task_id}_draft.md   : 原始草稿（调试用）
- {task_id}_tagged.md  : 占位符文本（调试用）
- {task_id}_log.json   : 完整运行日志
"""
import json
from pathlib import Path
from config.settings import OUTPUT_DIR, logger
from state.schema import AgentState


def write_outputs(task_id: str, final_state: AgentState) -> dict[str, Path]:
    """
    将 final_state 的关键内容写入 output/ 目录。
    返回: {输出类型: 文件路径} 字典
    """
    paths: dict[str, Path] = {}

    # 1. 最终 LaTeX
    if final_state.get("final_latex"):
        p = OUTPUT_DIR / f"{task_id}_final.tex"
        p.write_text(final_state["final_latex"], encoding="utf-8")
        paths["final_latex"] = p
        logger.info(f"[output] 导出 LaTeX: {p.name}")

    # 2. 原始草稿
    if final_state.get("draft_content"):
        p = OUTPUT_DIR / f"{task_id}_draft.md"
        p.write_text(final_state["draft_content"], encoding="utf-8")
        paths["draft"] = p

    # 3. 占位符文本
    if final_state.get("tagged_text"):
        p = OUTPUT_DIR / f"{task_id}_tagged.md"
        p.write_text(final_state["tagged_text"], encoding="utf-8")
        paths["tagged"] = p

    # 4. 运行日志
    log_data = {
        "task_id": task_id,
        "topic": final_state.get("topic", ""),
        "difficulty": final_state.get("difficulty", ""),
        "arbiter_decision": final_state.get("arbiter_decision", ""),
        "arbiter_feedback": final_state.get("arbiter_feedback", ""),
        "retry_count": final_state.get("retry_count", 0),
        "math_review": final_state.get("math_review", ""),
        "physics_review": final_state.get("physics_review", ""),
        "block_formula_count": len(final_state.get("formula_dict", {})),
        "inline_formula_count": len(final_state.get("inline_dict", {})),
        "has_final_output": bool(final_state.get("final_latex")),
    }
    p = OUTPUT_DIR / f"{task_id}_log.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    paths["log"] = p

    return paths
