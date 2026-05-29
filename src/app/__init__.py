"""
物理竞赛题全自动生成系统 — CLI 主入口。

使用方法:
    uv run physics-generator --topic "刚体力学"
    uv run physics-generator --topic "电磁感应" --difficulty "省级竞赛"
    uv run physics-generator --input task.json
    uv run physics-generator --adapt source.txt
    uv run physics-generator --adapt source.txt --mode idea_expansion

注：本系统不集成任何外部审题工具（如 ai-reviewer CLI）。所有审核
（数学 / 物理 / 结构 / 仲裁）均在状态机内由本仓库的 Agent 完成。

产物写盘、LaTeX 编译与 API 额度查询逻辑见 :mod:`app.outputs`；统一的任务
执行入口见 :mod:`app.runner`，由 CLI 与 API 后端共用。
"""
import uuid
import argparse
from pathlib import Path

from spec.normalizer import from_cli, from_json
from model.state import WorkflowData
from model.stats import get_all as get_run_stats, get_total_tokens
from app.runner import execute_task, RunResult
from config.config import (
    logger, BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, ARBITER_MAX_TOKENS,
    PROJECT_ROOT, OUTPUT_DIR, LLM_PROVIDER, LLM_STREAMING,
)


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

    # 动态遍历：按前缀分组，保证分阶段计数 / 多轮仲裁下的全部统计都能显示。
    def _sorted_keys(prefix: str) -> list[str]:
        import re as _re
        def _suffix_num(k: str) -> int:
            m = _re.search(r'(\d+)$', k)
            return int(m.group(1)) if m else 0
        return sorted([k for k in stats if k.startswith(prefix)], key=_suffix_num)

    ordered_keys: list[str] = []
    ordered_keys += _sorted_keys("planner")
    ordered_keys += _sorted_keys("problem_gen_r")
    ordered_keys += _sorted_keys("solution_gen_r")
    for k in ("math_check", "physics_check", "quality_check"):
        if k in stats:
            ordered_keys.append(k)
    ordered_keys += _sorted_keys("arbiter_r")
    for k in ("formatter", "template_agent"):
        if k in stats:
            ordered_keys.append(k)

    node_lines = []
    for key in ordered_keys:
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
- **streaming**: {LLM_STREAMING}

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
- **API 消耗估计**: {final_state.get('api_cost_usd') if final_state.get('api_cost_usd') is not None else '未取得'} USD

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


# ============ 控制台摘要 ============

def _print_summary(
    task_id: str,
    final_state: WorkflowData,
    output_paths: dict[str, Path],
    error_msg: str = "",
) -> None:
    """统一的结束摘要，供 --topic / --adapt / --input 三条路径共用。"""
    print(f"\n{'='*60}")
    print("任务执行完成" if not error_msg else "任务执行失败")
    print(f"   任务 ID:   {task_id}")
    if error_msg:
        print(f"   失败原因:   {error_msg}")
    print(f"   最终裁决:   {final_state.get('arbiter_decision', 'N/A')}")
    print(
        f"   重试次数:   {final_state.get('retry_count', 0)} "
        f"(命题 {final_state.get('problem_retry_count', 0)} / 解题 {final_state.get('solution_retry_count', 0)})"
    )
    print(f"   Block 公式: {len(final_state.get('formula_dict', {}))} 个")
    print(f"   Inline公式: {len(final_state.get('inline_dict', {}))} 个")
    tok = get_total_tokens()
    print(f"   Token用量:  prompt={tok['prompt_tokens']} + completion={tok['completion_tokens']} = {tok['total_tokens']}")
    if final_state.get("api_cost_usd") is not None:
        print(f"   API消耗:   {final_state.get('api_cost_usd')} USD")
    if output_paths:
        print("   输出文件:")
        for name, path in output_paths.items():
            print(f"     [{name}] {path}")
    print(f"{'='*60}")


# ============ 主函数 ============

def _run_and_report(
    initial_state: WorkflowData,
    *,
    topic: str,
    difficulty: str,
    write_log: bool,
) -> RunResult:
    """执行任务并完成 CLI 侧的日志/摘要输出。"""
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    logger.info(f"{'='*60}")
    logger.info(
        "系统启动 | topic=%s | difficulty=%s | total_score=%s | task_id=%s",
        topic[:60], difficulty, initial_state.get("total_score"), task_id,
    )
    logger.info(f"{'='*60}")

    logger.info("开始执行推理流（可能需要数分钟）...")
    result = execute_task(initial_state, task_id, OUTPUT_DIR)

    if write_log:
        _append_test_log(
            topic=topic, difficulty=difficulty,
            model=BIG_MODEL_NAME, max_tokens=BIG_MODEL_MAX_TOKENS,
            total_elapsed=result.elapsed,
            final_state=result.final_state, error_msg=result.error_msg,
        )

    _print_summary(task_id, result.final_state, result.output_paths, result.error_msg)
    return result


def main(topic: str, difficulty: str = "国家集训队", *,
         total_score: int = 40, write_log: bool = False,
         source_file: str | None = None,
         mode: str | None = None) -> None:
    """主函数：规格化输入 → 执行任务 → 写出产物与摘要。"""
    initial_state = from_cli(
        topic=topic,
        difficulty=difficulty,
        total_score=total_score,
        source_file=source_file,
        mode=mode,
    )
    _run_and_report(
        initial_state, topic=topic, difficulty=difficulty, write_log=write_log,
    )


def _cli() -> None:
    """CLI 入口点（pyproject.toml [project.scripts] 使用）。"""
    parser = argparse.ArgumentParser(
        prog="physics-generator",
        description="CPhO 物理竞赛题全自动生成与审核系统",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", type=str, help="物理主题（直接指定）")
    group.add_argument("--input", type=str, metavar="FILE",
                       help="从 JSON 文件加载任务（需含 topic 或 source_material 字段）")
    group.add_argument("--adapt", type=str, metavar="FILE",
                       help="基于已有材料改编（文件路径）")
    parser.add_argument("--difficulty", type=str, default="国家集训队",
                        help="难度等级（默认: 国家集训队）")
    parser.add_argument("--score", type=int, default=40,
                        help="题目总分（20-80，默认: 40）")
    parser.add_argument("--mode", type=str, default=None,
                        choices=["topic_generation", "literature_adaptation",
                                 "idea_expansion", "problem_enrichment"],
                        help="命题模式（默认自动推断）")
    parser.add_argument("--log", action="store_true",
                        help="追加运行记录到 TEST_LOG.md")

    args = parser.parse_args()

    if args.input:
        data = from_json(args.input)
        _run_and_report(
            data,
            topic=data.get("topic", ""),
            difficulty=data.get("difficulty", ""),
            write_log=args.log,
        )
    elif args.adapt:
        topic = Path(args.adapt).stem
        main(topic, args.difficulty, total_score=args.score,
             write_log=args.log, source_file=args.adapt, mode=args.mode)
    else:
        main(args.topic, args.difficulty, total_score=args.score,
             write_log=args.log, mode=args.mode)


if __name__ == "__main__":
    _cli()
