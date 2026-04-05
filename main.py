"""
物理竞赛题全自动生成系统 — 主入口。

使用方法:
    python main.py                          # 从 topics.js 随机选题
    python main.py task_001.json            # 从 JSON 文件指定主题

运行前提:
    1. 已创建 .env 文件（从 .env.example 复制并填入 API Key）
    2. 已安装依赖: pip install -r requirements.txt
"""
import sys
import uuid
import time
from io_.input_loader import load_task
from io_.output_writer import write_outputs
from graph.workflow import build_graph
from state.schema import AgentState
from config.settings import logger
from config.topics import get_random_topic
from utils.run_stats import get_all as get_run_stats, get_total_tokens, clear as clear_run_stats
from config.settings import (
    BIG_MODEL_NAME, BIG_MODEL_MAX_TOKENS, ARBITER_MAX_TOKENS,
    SMALL_MODEL_NAME, SMALL_MODEL_MAX_TOKENS, PROJECT_ROOT,
)


def _next_run_number() -> int:
    """读取 TEST_LOG.md，找到最大 Run # 编号 +1。"""
    import re
    log_path = PROJECT_ROOT / "TEST_LOG.md"
    if not log_path.exists():
        return 1
    text = log_path.read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"## Run #(\d+)", text)]
    return max(nums, default=0) + 1


def _append_test_log(
    topic: str,
    difficulty: str,
    model: str,
    max_tokens: int,
    total_elapsed: float,
    final_state: dict,
    error_msg: str,
) -> None:
    """自动追加一条运行记录到 TEST_LOG.md。"""
    from datetime import datetime

    stats = get_run_stats()
    run_num = _next_run_number()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "\u274c \u5931\u8d25" if error_msg else "\u2705 \u6210\u529f"

    # 各节点统计行
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
            node_lines.append(f"- {key}: {s['chars']} \u5b57\u7b26 ({s['elapsed']:.0f}s){tok_info}{extra}")

    nodes_text = "\n".join(node_lines) if node_lines else "- \uff08\u65e0\u6570\u636e\uff09"

    # Token 汇总
    tok = get_total_tokens()
    token_text = (
        f"- **Prompt tokens**: {tok['prompt_tokens']}\n"
        f"- **Completion tokens**: {tok['completion_tokens']}\n"
        f"- **Total tokens**: {tok['total_tokens']}"
    )

    entry = f"""
---

## Run #{run_num}

- **\u65f6\u95f4**: {now}
- **\u6a21\u578b**: `{model}`
- **max_tokens**: {max_tokens} (arbiter: {ARBITER_MAX_TOKENS})
- **streaming**: True

### \u8f93\u5165
- \u6a21\u5f0f: \u968f\u673a\u9009\u9898 (topics.js)
- \u4e3b\u9898: {topic[:80]}{'...' if len(topic) > 80 else ''}
- \u96be\u5ea6: {difficulty}

### \u6a21\u578b\u8f93\u51fa
{nodes_text}

### \u7ed3\u679c
- **\u72b6\u6001**: {status}
- **\u5931\u8d25\u539f\u56e0**: {error_msg if error_msg else '\u65e0'}
- **\u603b\u8017\u65f6**: {total_elapsed:.0f}s
- **\u6700\u7ec8\u88c1\u51b3**: {final_state.get('arbiter_decision', 'N/A')}
- **\u91cd\u8bd5\u6b21\u6570**: {final_state.get('retry_count', 0)}
- **Block \u516c\u5f0f**: {len(final_state.get('formula_dict', {}))} \u4e2a
- **Inline \u516c\u5f0f**: {len(final_state.get('inline_dict', {}))} \u4e2a

### Token \u7528\u91cf
{token_text}

### \u5907\u6ce8

"""
    log_path = PROJECT_ROOT / "TEST_LOG.md"
    if not log_path.exists():
        log_path.write_text("# \u6d4b\u8bd5\u8fd0\u884c\u65e5\u5fd7\n", encoding="utf-8")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
    logger.info(f"[TEST_LOG] Run #{run_num} \u5df2\u8ffd\u52a0\u5230 {log_path}")


def main(task_filename: str | None = None) -> None:
    """主函数：加载 → 构建图 → 执行 → 写出"""

    # ===== Step 1: 加载输入 =====
    logger.info(f"{'='*60}")

    if task_filename:
        # 兼容旧模式：从 JSON 文件读取
        logger.info(f"系统启动 | 任务文件: {task_filename}")
        task_data = load_task(task_filename)
        task_id = task_data["task_id"]
        topic = task_data["topic"]
        difficulty = task_data["difficulty"]
    else:
        # 新模式：从 topics.js 随机选题
        logger.info("系统启动 | 模式: 随机选题 (topics.js)")
        topic = get_random_topic()
        difficulty = "国家集训队"
        task_id = f"random_{uuid.uuid4().hex[:8]}"

    logger.info(f"{'='*60}")

    # ===== Step 2: 构建初始 State =====
    initial_state: AgentState = {
        "topic": topic,
        "difficulty": difficulty,
        "draft_content": "",
        "math_review": "",
        "physics_review": "",
        "arbiter_decision": "",
        "arbiter_feedback": "",
        "retry_count": 0,
        "formula_dict": {},
        "inline_dict": {},
        "tagged_text": "",
        "formatted_text": "",
        "final_latex": "",
    }

    # ===== Step 3: 构建并运行工作流 =====
    logger.info("构建工作流状态图...")
    compiled_graph = build_graph()
    clear_run_stats()

    logger.info("开始执行推理流（可能需要数分钟）...")
    t_start = time.time()
    error_msg = ""
    final_state = initial_state  # fallback

    try:
        final_state = compiled_graph.invoke(initial_state)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"图执行异常: {error_msg}")

    total_elapsed = time.time() - t_start

    # ===== Step 4: 写出结果 =====
    output_paths = {}
    if not error_msg:
        logger.info("推理完成，导出产物...")
        output_paths = write_outputs(task_id, final_state)

    # ===== Step 5: 自动追加 TEST_LOG.md =====
    _append_test_log(
        topic=topic,
        difficulty=difficulty,
        model=BIG_MODEL_NAME,
        max_tokens=BIG_MODEL_MAX_TOKENS,
        total_elapsed=total_elapsed,
        final_state=final_state,
        error_msg=error_msg,
    )

    # ===== 最终汇总（唯一允许 print 的地方） =====
    print(f"\n{'='*60}")
    print("✅ 任务执行完成！")
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


if __name__ == "__main__":
    task_file = sys.argv[1] if len(sys.argv) > 1 else None
    main(task_file)
