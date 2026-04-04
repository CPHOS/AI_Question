"""
全局配置中心。
所有常量、路径、模型参数、正则表达式、日志配置均集中于此。
禁止在其他文件中出现硬编码的魔术字符串或路径。
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# ============ 加载 .env ============
load_dotenv()

# ============ 日志配置（全局唯一） ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s.%(module)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PhysicsGenerator")

# ============ 路径配置 ============
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
INPUT_DIR: Path = PROJECT_ROOT / "input"
OUTPUT_DIR: Path = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============ 环境变量安全读取 ============
def _get_env(key: str) -> str:
    """读取环境变量，缺失时立即报错并给出修复指引。"""
    val = os.getenv(key)
    if not val:
        raise ValueError(
            f"环境变量缺失: '{key}'。\n"
            f"  修复方法: 复制 .env.example 为 .env 并填入真实值。\n"
            f"  执行命令: copy .env.example .env  (Windows)"
        )
    return val

# ============ 大模型配置（命题 / 验算 / 仲裁） ============
BIG_MODEL_API_KEY: str = _get_env("BIG_MODEL_API_KEY")
BIG_MODEL_BASE_URL: str = _get_env("BIG_MODEL_BASE_URL")
BIG_MODEL_NAME: str = _get_env("BIG_MODEL_NAME")
BIG_MODEL_TEMPERATURE: float = 0.7       # 命题时允许创造性
BIG_MODEL_MAX_TOKENS: int = 8192         # 竞赛题解答较长，需要足够 token

# ============ 小模型配置（格式化排版） ============
SMALL_MODEL_API_KEY: str = _get_env("SMALL_MODEL_API_KEY")
SMALL_MODEL_BASE_URL: str = _get_env("SMALL_MODEL_BASE_URL")
SMALL_MODEL_NAME: str = _get_env("SMALL_MODEL_NAME")
SMALL_MODEL_TEMPERATURE: float = 0.0     # 排版零创造性
SMALL_MODEL_MAX_TOKENS: int = 8192

# ============ 流程控制 ============
MAX_RETRY_COUNT: int = 3                  # 仲裁最大重试轮数（含首次）

# ============ 占位符前后缀 ============
BLOCK_PLACEHOLDER_PREFIX: str = "{{BLOCK_MATH_"
BLOCK_PLACEHOLDER_SUFFIX: str = "}}"
INLINE_PLACEHOLDER_PREFIX: str = "{{INLINE_MATH_"
INLINE_PLACEHOLDER_SUFFIX: str = "}}"

# ============ 核心正则表达式 ============
# Block 公式: <block_math label="eq:xxx"> ... </block_math>（跨行匹配）
# 同时兼容正确的 </block_math> 和 LLM 常见错误 \end{block_math}
BLOCK_MATH_PATTERN: str = r'<block_math\s+label="([^"]+)">\s*(.*?)\s*(?:</block_math>|\\end\{block_math\})'
# Inline 公式: $...$（非贪婪，排除转义的 \$）
INLINE_MATH_PATTERN: str = r'(?<!\\)\$(.+?)(?<!\\)\$'
