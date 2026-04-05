"""
YAML 提示词加载器。
所有 Agent 的 System Prompt 和 User Prompt 模板统一由 YAML 文件管理。

使用方法:
    from prompts import load

    # 加载系统提示词（无变量替换）
    system_prompt = load("generator", "system_prompt")

    # 加载用户提示词（带变量替换）
    user_prompt = load("generator", "user_prompt_initial", topic="电磁感应", difficulty="国家集训队")

变量替换机制:
    使用 str.replace() 逐一替换 {variable}。
    仅替换 kwargs 中显式传入的 key，其余花括号（如 LaTeX 的 \\begin{equation}、
    占位符 {{BLOCK_MATH_X}} 等）不受影响，无需转义。
"""
import yaml
from pathlib import Path
from functools import lru_cache

_PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def _load_yaml(name: str) -> dict[str, str]:
    """加载并缓存指定 YAML 文件。"""
    path = _PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"提示词文件未找到: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load(agent: str, key: str, **kwargs) -> str:
    """
    加载提示词模板，可选变量替换。

    参数:
        agent: YAML 文件名（不含 .yaml 后缀），如 "generator", "verifier"
        key:   YAML 中的顶层 key，如 "system_prompt", "user_prompt"
        **kwargs: 替换变量，如 topic="电磁感应"

    返回:
        替换后的提示词字符串
    """
    data = _load_yaml(agent)
    if key not in data:
        raise KeyError(f"提示词 key '{key}' 未在 {agent}.yaml 中找到")
    text = data[key]
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text
