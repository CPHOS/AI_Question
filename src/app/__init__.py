"""
物理竞赛题全自动生成系统 — 主入口。

使用方法:
    uv run physics-generator --topic "刚体力学"
    uv run physics-generator --topic "电磁感应" --difficulty "省级竞赛"
    uv run physics-generator --input task.json
"""
import sys
import json
import uuid
import time
import argparse
from pathlib import Path

from graph.workflow import build_graph
from model.state import AgentState
from model.stats import get_all as get_run_stats, get_total_tokens, clear as clear_run_stats
from config.settings import (
    logger, BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, ARBITER_MAX_TOKENS,
    SMALL_MODEL_NAME, SMALL_MODEL_MAX_TOKENS, PROJECT_ROOT, OUTPUT_DIR,
)


# ============ 输出写入 ============

def _write_outputs(task_id: str, final_state: AgentState) -> dict[str, Path]:
    """将 final_state 的关键内容写入 output/ 目录。"""
    paths: dict[str, Path] = {}

    if final_state.get("final_latex"):
        p = OUTPUT_DIR / f"{task_id}_final.tex"
        p.write_text(final_state["final_latex"], encoding="utf-8")
        paths["final_latex"] = p
        logger.info(f"[output] 导出 LaTeX: {p.name}")

    if final_state.get("draft_content"):
        p = OUTPUT_DIR / f"{task_id}_draft.md"
        p.write_text(final_state["draft_content"], encoding="utf-8")
        paths["draft"] = p

    if final_state.get("tagged_text"):
        p = OUTPUT_DIR / f"{task_id}_tagged.md"
        p.write_text(final_state["tagged_text"], encoding="utf-8")
        paths["tagged"] = p

    log_data = {
        "task_id": task_id,
        "topic": final_state.get("topic", ""),
        "difficulty": final_state.get("difficulty", ""),
        "total_score": final_state.get("total_score", 0),
        "arbiter_decision": final_state.get("arbiter_decision", ""),
        "arbiter_reason": final_state.get("arbiter_reason", ""),
        "error_category": final_state.get("error_category", ""),
        "arbiter_feedback": final_state.get("arbiter_feedback", ""),
        "retry_count": final_state.get("retry_count", 0),
        "math_review": final_state.get("math_review", ""),
        "physics_review": final_state.get("physics_review", ""),
        "block_formula_count": len(final_state.get("formula_dict", {})),
        "inline_formula_count": len(final_state.get("inline_dict", {})),
        "figure_count": len(final_state.get("figure_descriptions", {})),
        "has_final_output": bool(final_state.get("final_latex")),
    }
    p = OUTPUT_DIR / f"{task_id}_log.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    paths["log"] = p

    # ===== 仲裁报告 =====
    decision = final_state.get("arbiter_decision", "N/A")
    decision_label = {
        "PASS": "✅ 通过",
        "PASS_WITH_EDITS": "⚠️ 有条件通过（仅存在用语规范问题，需人工修订）",
        "RETRY": "🔄 重试未通过",
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
        f"## 仲裁结果\n\n",
        f"- **裁决**: {decision_label}\n",
        f"- **错误类别**: {final_state.get('error_category', 'N/A')}\n",
        f"- **理由**: {final_state.get('arbiter_reason', '')}\n",
        f"- **重试次数**: {final_state.get('retry_count', 0)}\n\n",
        f"## 数学审核意见\n\n",
        f"{final_state.get('math_review', '无')}\n\n",
        f"## 物理审核意见\n\n",
        f"{final_state.get('physics_review', '无')}\n\n",
        f"## 仲裁反馈\n\n",
        f"{final_state.get('arbiter_feedback', '无')}\n",
    ]
    p = OUTPUT_DIR / f"{task_id}_report.md"
    p.write_text("".join(report_lines), encoding="utf-8")
    paths["report"] = p
    logger.info(f"[output] 导出仲裁报告: {p.name}")

    # ===== 图片绘制需求 =====
    fig_desc = final_state.get("figure_descriptions", {})
    if fig_desc:
        assets_dir = OUTPUT_DIR / f"{task_id}_assets"
        assets_dir.mkdir(exist_ok=True)
        lines = ["# 图片绘制需求\n\n"]
        for fig_name in sorted(fig_desc.keys()):
            data = fig_desc[fig_name]
            lines.append(f"## {data['filename']} — {data['caption']}\n\n")
            lines.append(f"{data['description']}\n\n")
        p = assets_dir / "README.md"
        p.write_text("".join(lines), encoding="utf-8")
        paths["figure_descriptions"] = p
        logger.info(f"[output] 导出图片需求: {p}")

    return paths


# ============ 运行日志（可选） ============

def _next_run_number() -> int:
    import re
    log_path = PROJECT_ROOT / "TEST_LOG.md"
    if not log_path.exists():
        return 1
    text = log_path.read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"## Run #(\d+)", text)]
    return max(nums, default=0) + 1


def _append_test_log(
    topic: str, difficulty: str, model: str, max_tokens: int,
    total_elapsed: float, final_state: dict, error_msg: str,
) -> None:
    from datetime import datetime

    stats = get_run_stats()
    run_num = _next_run_number()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "❌ 失败" if error_msg else "✅ 成功"

    node_lines = []
    for key in ["generator_r0", "generator_r1", "generator_r2",
                 "math_verifier", "physics_verifier",
                 "arbiter_r1", "arbiter_r2", "arbiter_r3",
                 "formatter"]:
        if key in stats:
            s = stats[key]
            extra = f" ({s['extra']})" if s.get("extra") else ""
            tok_info = ""
            if s.get("total_tokens"):
                tok_info = f" | tokens: {s['prompt_tokens']}+{s['completion_tokens']}={s['total_tokens']}"
            node_lines.append(f"- {key}: {s['chars']} 字符 ({s['elapsed']:.0f}s){tok_info}{extra}")

    nodes_text = "\n".join(node_lines) if node_lines else "- （无数据）"
    tok = get_total_tokens()
    token_text = (
        f"- **Prompt tokens**: {tok['prompt_tokens']}\n"
        f"- **Completion tokens**: {tok['completion_tokens']}\n"
        f"- **Total tokens**: {tok['total_tokens']}"
    )

    entry = f"""
---

## Run #{run_num}

- **时间**: {now}
- **模型**: `{model}`
- **max_tokens**: {max_tokens} (arbiter: {ARBITER_MAX_TOKENS})
- **streaming**: True

### 输入
- 主题: {topic[:80]}{'...' if len(topic) > 80 else ''}
- 难度: {difficulty}

### 模型输出
{nodes_text}

### 结果
- **状态**: {status}
- **失败原因**: {error_msg if error_msg else '无'}
- **总耗时**: {total_elapsed:.0f}s
- **最终裁决**: {final_state.get('arbiter_decision', 'N/A')}
- **重试次数**: {final_state.get('retry_count', 0)}
- **Block 公式**: {len(final_state.get('formula_dict', {}))} 个
- **Inline 公式**: {len(final_state.get('inline_dict', {}))} 个

### Token 用量
{token_text}

### 备注

"""
    log_path = PROJECT_ROOT / "TEST_LOG.md"
    if not log_path.exists():
        log_path.write_text("# 测试运行日志\n", encoding="utf-8")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
    logger.info(f"[TEST_LOG] Run #{run_num} 已追加到 {log_path}")


# ============ 输入加载 ============

def _load_input_json(filepath: str) -> dict:
    """从 JSON 文件加载任务。"""
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"任务文件未找到: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    for field in ("topic", "difficulty"):
        if field not in data:
            raise KeyError(f"JSON 缺少必填字段: '{field}'，文件: {p}")
    return data


# ============ 主函数 ============

def main(topic: str, difficulty: str = "国家集训队", *,
         total_score: int = 50, write_log: bool = False) -> None:
    """主函数：构建图 → 执行 → 写出"""

    task_id = f"task_{uuid.uuid4().hex[:8]}"
    logger.info(f"{'='*60}")
    logger.info(f"系统启动 | topic={topic[:60]} | difficulty={difficulty} | total_score={total_score} | task_id={task_id}")
    logger.info(f"{'='*60}")

    initial_state: AgentState = {
        "topic": topic,
        "difficulty": difficulty,
        "total_score": total_score,
        "title": "",
        "draft_content": "",
        "math_review": "",
        "physics_review": "",
        "arbiter_decision": "",
        "arbiter_reason": "",
        "arbiter_feedback": "",
        "error_category": "",
        "retry_count": 0,
        "formula_dict": {},
        "inline_dict": {},
        "tagged_text": "",
        "formatted_text": "",
        "final_latex": "",
        "figure_dict": {},
        "figure_descriptions": {},
    }

    logger.info("构建工作流状态图...")
    compiled_graph = build_graph()
    clear_run_stats()

    logger.info("开始执行推理流（可能需要数分钟）...")
    t_start = time.time()
    error_msg = ""
    final_state = initial_state

    try:
        final_state = compiled_graph.invoke(initial_state)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"图执行异常: {error_msg}")

    total_elapsed = time.time() - t_start

    output_paths = {}
    if not error_msg:
        logger.info("推理完成，导出产物...")
        output_paths = _write_outputs(task_id, final_state)

    if write_log:
        _append_test_log(
            topic=topic, difficulty=difficulty,
            model=BIG_MODEL_NAME, max_tokens=BIG_MODEL_MAX_TOKENS,
            total_elapsed=total_elapsed,
            final_state=final_state, error_msg=error_msg,
        )

    print(f"\n{'='*60}")
    print("任务执行完成")
    print(f"   任务 ID:   {task_id}")
    print(f"   最终裁决:   {final_state.get('arbiter_decision', 'N/A')}")
    print(f"   重试次数:   {final_state.get('retry_count', 0)}")
    print(f"   Block 公式: {len(final_state.get('formula_dict', {}))} 个")
    print(f"   Inline公式: {len(final_state.get('inline_dict', {}))} 个")
    tok = get_total_tokens()
    print(f"   Token用量:  prompt={tok['prompt_tokens']} + completion={tok['completion_tokens']} = {tok['total_tokens']}")
    print("   输出文件:")
    for name, path in output_paths.items():
        print(f"     [{name}] {path}")
    print(f"{'='*60}")


def _cli() -> None:
    """CLI 入口点（pyproject.toml [project.scripts] 使用）。"""
    parser = argparse.ArgumentParser(
        prog="physics-generator",
        description="CPhO 物理竞赛题全自动生成与审核系统",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", type=str, help="物理主题（直接指定）")
    group.add_argument("--input", type=str, metavar="FILE",
                       help="从 JSON 文件加载任务（需含 topic, difficulty 字段）")
    parser.add_argument("--difficulty", type=str, default="国家集训队",
                        help="难度等级（默认: 国家集训队）")
    parser.add_argument("--score", type=int, default=40,
                        help="题目总分（20-80，默认: 40）")
    parser.add_argument("--log", action="store_true",
                        help="追加运行记录到 TEST_LOG.md")

    args = parser.parse_args()

    if args.input:
        data = _load_input_json(args.input)
        topic = data["topic"]
        difficulty = data.get("difficulty", args.difficulty)
        total_score = data.get("total_score", args.score)
    else:
        topic = args.topic
        difficulty = args.difficulty
        total_score = args.score

    main(topic, difficulty, total_score=total_score, write_log=args.log)


if __name__ == "__main__":
    _cli()
