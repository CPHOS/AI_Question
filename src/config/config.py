"""
全局配置中心。

分层原则：
  - **部署 / 基础设施 / 机密**（API 监听、数据库路径、CORS、引导管理员 token、
    宿主 LaTeX 工具链、服务商 API Key 种子等）通过 ``.env`` 环境变量管理。
  - **模型与服务商选择及其参数、流程开关**等「业务可调项」不再以环境变量编码，
    而是以可持久化记录存入数据库，仅通过管理员 API 修改（见
    :mod:`api.settings_store` 与 :mod:`config.runtime`）。本文件中带 ``SEED_``
    前缀的常量仅作为数据库首次初始化时的种子默认值。

禁止在其他文件中出现硬编码的魔术字符串或路径。
"""
import os
import logging
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from dotenv import load_dotenv

# ============ 加载 .env ============
load_dotenv()

# ============ 应用元信息（版本号单一来源） ============
# 版本号以已安装发行包的元数据为准（即 pyproject.toml 的 ``version``），
# 避免在多处硬编码。未以包形式安装（如直接源码运行）时回退到 _FALLBACK_VERSION。
APP_NAME: str = "physics-generator"
_FALLBACK_VERSION: str = "0.1.0"
try:
    APP_VERSION: str = _pkg_version(APP_NAME)
except PackageNotFoundError:
    APP_VERSION = _FALLBACK_VERSION
# SPDX license 标识，与 pyproject.toml / LICENSE 保持一致。
APP_LICENSE: str = "AGPL-3.0-or-later"

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

# ============ 通用解析 helper ============
def _env_bool(key: str, default: bool) -> bool:
    """解析布尔环境变量；缺失时返回 default。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# ============ 模型 / 服务商：种子默认值（SEED ONLY） ============
# 重要：模型与服务商的选择及其参数现以**可持久化记录**形式存储于数据库，
# 并且**只能通过管理员 API**修改（见 :mod:`api.settings_store` 与
# :mod:`config.runtime`）。以下环境变量仅在数据库尚无配置时作为「首次启动种子」
# 写入，之后一律以数据库记录为准——请勿在业务代码中直接读取这些常量做决策。
SEED_LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
SEED_OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
SEED_LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
SEED_LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")

SEED_BIG_MODEL_NAME: str = os.getenv("BIG_MODEL_NAME", "")
SEED_BIG_MODEL_TEMPERATURE: float = float(os.getenv("BIG_MODEL_TEMPERATURE", "0.7"))
SEED_BIG_MODEL_MAX_TOKENS: int = int(os.getenv("BIG_MODEL_MAX_TOKENS", "32768"))
SEED_ARBITER_MAX_TOKENS: int = int(os.getenv("ARBITER_MAX_TOKENS", "4096"))

SEED_SMALL_MODEL_NAME: str = os.getenv("SMALL_MODEL_NAME", "")
SEED_SMALL_MODEL_TEMPERATURE: float = float(os.getenv("SMALL_MODEL_TEMPERATURE", "0.0"))
SEED_SMALL_MODEL_MAX_TOKENS: int = int(os.getenv("SMALL_MODEL_MAX_TOKENS", "8192"))

SEED_MODEL_TIMEOUT: int = int(os.getenv("MODEL_TIMEOUT", "600"))
SEED_LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
SEED_LLM_STREAMING: bool = _env_bool("LLM_STREAMING", False)

# 审核类 Agent 历史上固定使用 temperature=0.0（确定性审核）；作为种子默认。
SEED_REVIEW_TEMPERATURE: float = float(os.getenv("REVIEW_TEMPERATURE", "0.0"))

# ============ 流程控制 / 源材料 / 自动编译：种子默认值（SEED ONLY） ============
# 同样迁移为可持久化的运行期设置（app_settings 表），通过管理员 API 调整。
SEED_MAX_RETRY_COUNT: int = int(os.getenv("MAX_RETRY_COUNT", "3"))
SEED_SOURCE_MATERIAL_MAX_CHARS: int = int(os.getenv("SOURCE_MATERIAL_MAX_CHARS", "60000"))
SEED_AUTO_COMPILE_FIGURES: bool = _env_bool("AUTO_COMPILE_FIGURES", True)
SEED_AUTO_COMPILE_LATEX: bool = _env_bool("AUTO_COMPILE_LATEX", False)
# 单次 LaTeX / TikZ 编译子进程的超时（秒）。复杂模板或大图可调高。
SEED_LATEX_COMPILE_TIMEOUT: int = int(os.getenv("LATEX_COMPILE_TIMEOUT", "180"))
# LaTeX 编译后端：``local`` 用本机 subprocess 调 LATEX_ENGINE；``remote`` 把编译
# 卸载到独立编译服务（见 docs/latex-service-protocol.md）。
SEED_LATEX_COMPILER_BACKEND: str = os.getenv("LATEX_COMPILER_BACKEND", "local")
# 远程编译服务根 URL（backend=remote 时必填），如 http://latex-svc:8100
SEED_LATEX_SERVICE_BASE_URL: str = os.getenv("LATEX_SERVICE_BASE_URL", "")
# 远程编译作业的轮询间隔（秒）与客户端等待终态的总截止（秒）。
SEED_LATEX_SERVICE_POLL_INTERVAL: float = float(os.getenv("LATEX_SERVICE_POLL_INTERVAL", "2.0"))
SEED_LATEX_SERVICE_MAX_WAIT: float = float(os.getenv("LATEX_SERVICE_MAX_WAIT", "1200.0"))
# SSE 实时进度推送的轮询间隔与最长持续时间（秒）。
SEED_SSE_POLL_INTERVAL: float = float(os.getenv("SSE_POLL_INTERVAL", "1.0"))
SEED_SSE_MAX_DURATION: float = float(os.getenv("SSE_MAX_DURATION", "1800.0"))

# ============ LaTeX 编译设置（宿主工具链，保留为 .env 部署项） ============
LATEX_ENGINE: str = os.getenv("LATEX_ENGINE", "xelatex")
CPHOS_TEMPLATE_DIR: str = os.getenv(
    "CPHOS_TEMPLATE_DIR",
    str((PROJECT_ROOT.parent / "CPHOS-Latex" / "theory").resolve()),
)

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
# 跨域来源（逗号分隔）。留空则不启用 CORS（推荐用 nginx 反代做同源部署）。
# 例: CORS_ALLOW_ORIGINS=http://localhost:8080,https://dashboard.example.com
CORS_ALLOW_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()
]

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
