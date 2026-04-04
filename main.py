"""
物理竞赛题全自动生成系统 — 主入口。

使用方法:
    python main.py                          # 默认运行 task_001.json
    python main.py task_002.json            # 指定任务文件

运行前提:
    1. 已创建 .env 文件（从 .env.example 复制并填入 API Key）
    2. 已安装依赖: pip install -r requirements.txt
    3. input/ 目录下存在对应 JSON 文件
"""
import sys
from io_.input_loader import load_task
from io_.output_writer import write_outputs
from graph.workflow import build_graph
from state.schema import AgentState
from config.settings import logger


def main(task_filename: str = "task_001.json") -> None:
    """主函数：加载 → 构建图 → 执行 → 写出"""

    # ===== Step 1: 加载输入 =====
    logger.info(f"{'='*60}")
    logger.info(f"系统启动 | 任务文件: {task_filename}")
    logger.info(f"{'='*60}")

    task_data = load_task(task_filename)
    task_id = task_data["task_id"]

    # ===== Step 2: 构建初始 State =====
    initial_state: AgentState = {
        "topic": task_data["topic"],
        "difficulty": task_data["difficulty"],
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

    # ===== Step 3: 构建并运行 LangGraph =====
    logger.info("构建 LangGraph 状态图...")
    compiled_graph = build_graph()

    logger.info("开始执行推理流（可能需要数分钟）...")
    # recursion_limit=30: 每轮重试约 5 步 × 最多 3 轮 = 15 步 + 后处理 3 步 = 18 步，留有余量
    final_state = compiled_graph.invoke(
        initial_state,
        config={"recursion_limit": 30},
    )

    # ===== Step 4: 写出结果 =====
    logger.info("推理完成，导出产物...")
    output_paths = write_outputs(task_id, final_state)

    # ===== 最终汇总（唯一允许 print 的地方） =====
    print(f"\n{'='*60}")
    print("✅ 任务执行完成！")
    print(f"   任务 ID:   {task_id}")
    print(f"   最终裁决:   {final_state.get('arbiter_decision', 'N/A')}")
    print(f"   重试次数:   {final_state.get('retry_count', 0)}")
    print(f"   Block 公式: {len(final_state.get('formula_dict', {}))} 个")
    print(f"   Inline公式: {len(final_state.get('inline_dict', {}))} 个")
    print("   输出文件:")
    for name, path in output_paths.items():
        print(f"     [{name}] {path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    task_file = sys.argv[1] if len(sys.argv) > 1 else "task_001.json"
    main(task_file)
