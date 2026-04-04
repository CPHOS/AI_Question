"""
输入加载器：从 input/ 目录读取 JSON 任务文件。

JSON 格式规范:
{
    "task_id": "task_001",           // 必填，用于输出文件命名
    "topic": "刚体力学...",           // 必填，物理主题
    "difficulty": "国家集训队"        // 必填，难度等级
}
"""
import json
from pathlib import Path
from config.settings import INPUT_DIR, logger


def load_task(task_filename: str) -> dict:
    """
    加载指定任务文件。

    参数: task_filename — 文件名（含 .json 扩展名）
    返回: dict，包含 task_id, topic, difficulty
    异常: FileNotFoundError / KeyError / json.JSONDecodeError
    """
    filepath: Path = INPUT_DIR / task_filename

    if not filepath.exists():
        raise FileNotFoundError(f"任务文件未找到: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["task_id", "topic", "difficulty"]
    for field in required:
        if field not in data:
            raise KeyError(f"JSON 缺少必填字段: '{field}'，文件: {filepath}")

    logger.info(f"[input] 加载成功: {task_filename} | topic={data['topic']}")
    return data
