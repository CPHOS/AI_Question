"""
全局配置中心。
基于 .env 文件进行配置管理，所有可调参数均通过环境变量读取。
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
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "output")))
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

# ============ 服务商配置 ============
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")

# OpenRouter
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# 通用 OpenAI 兼容（DeepSeek、本地部署等）
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")

# 大模型配置（命题 / 验算 / 仲裁）
BIG_MODEL_NAME: str = _get_env("BIG_MODEL_NAME")
BIG_MODEL_TEMPERATURE: float = float(os.getenv("BIG_MODEL_TEMPERATURE", "0.7"))
BIG_MODEL_MAX_TOKENS: int = int(os.getenv("BIG_MODEL_MAX_TOKENS", "32768"))
ARBITER_MAX_TOKENS: int = int(os.getenv("ARBITER_MAX_TOKENS", "4096"))

# 小模型配置（格式化排版）
SMALL_MODEL_NAME: str = _get_env("SMALL_MODEL_NAME")
SMALL_MODEL_TEMPERATURE: float = float(os.getenv("SMALL_MODEL_TEMPERATURE", "0.0"))
SMALL_MODEL_MAX_TOKENS: int = int(os.getenv("SMALL_MODEL_MAX_TOKENS", "8192"))

# 通用超时设置
MODEL_TIMEOUT: int = int(os.getenv("MODEL_TIMEOUT", "600"))
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_STREAMING: bool = os.getenv("LLM_STREAMING", "false").lower() not in {
    "0", "false", "no", "off"
}

# ============ LaTeX 编译设置 ============
LATEX_ENGINE: str = os.getenv("LATEX_ENGINE", "xelatex")
AUTO_COMPILE_FIGURES: bool = os.getenv("AUTO_COMPILE_FIGURES", "true").lower() not in {
    "0", "false", "no", "off"
}
AUTO_COMPILE_LATEX: bool = os.getenv("AUTO_COMPILE_LATEX", "false").lower() not in {
    "0", "false", "no", "off"
}
CPHOS_TEMPLATE_DIR: str = os.getenv(
    "CPHOS_TEMPLATE_DIR",
    str((PROJECT_ROOT.parent / "CPHOS-Latex" / "theory").resolve()),
)

# ============ 流程控制 ============
MAX_RETRY_COUNT: int = int(os.getenv("MAX_RETRY_COUNT", "3"))

# ============ 源材料控制 ============
SOURCE_MATERIAL_MAX_CHARS: int = int(os.getenv("SOURCE_MATERIAL_MAX_CHARS", "60000"))

# ============ API 后端服务 ============
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))
# 同时执行的生成任务上限
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
# 用户 / token / 任务元数据持久化数据库（相对路径按项目根目录解析）
_db_path_raw: str = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "api.db"))
DB_PATH: Path = Path(_db_path_raw) if Path(_db_path_raw).is_absolute() else PROJECT_ROOT / _db_path_raw
# 引导管理员 token（明文）；首次启动且库内无 admin 时写入其哈希。留空则不创建。
ADMIN_BOOTSTRAP_TOKEN: str = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "")

# ============ 占位符前后缀 ============
BLOCK_PLACEHOLDER_PREFIX: str = "{{BLOCK_MATH_"
BLOCK_PLACEHOLDER_SUFFIX: str = "}}"
INLINE_PLACEHOLDER_PREFIX: str = "{{INLINE_MATH_"
INLINE_PLACEHOLDER_SUFFIX: str = "}}"

# ============ 核心正则表达式 ============
# Block 公式: <block_math label="eq:xxx" score="3"> ... </block_math>（跨行匹配，score 可选）
# 同时兼容正确的 </block_math> 和 LLM 常见错误 \end{block_math}
BLOCK_MATH_PATTERN: str = r'<block_math\s+label="([^"]+)"(?:\s+score="(\d+)")?\s*>\s*(.*?)\s*(?:</block_math>|\\end\{block_math\})'
# Inline 公式: $...$（非贪婪，排除转义的 \$）
INLINE_MATH_PATTERN: str = r'(?<!\\)\$(.+?)(?<!\\)\$'

# Fallback: 当 <block_math> 一个都匹配不到时，尝试提取 $$...$$ 作为 block 公式
FALLBACK_BLOCK_PATTERN: str = r'\$\$\s*(.+?)\s*\$\$'

# Figure: <figure label="fig:xxx" caption="描述"> 绘图说明 </figure>（跨行匹配）
FIGURE_PATTERN: str = r'<figure\s+label="([^"]+)"\s+caption="([^"]*)"\s*>\s*(.*?)\s*</figure>'
FIGURE_PLACEHOLDER_PREFIX: str = "{{FIGURE_"
FIGURE_PLACEHOLDER_SUFFIX: str = "}}"
