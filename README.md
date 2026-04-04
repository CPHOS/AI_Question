# 🏋️ 物理竞赛题全自动生成与多重审核系统

基于 **LangGraph** 状态机编排的 AI Agent 系统，自动生成高质量物理竞赛题并通过多轮审核确保正确性。

## 架构亮点

- **双重公式隔离**：Block（独立公式）+ Inline（行内符号）分别提取，小模型永远不碰数学
- **三 Agent 审核闭环**：数学教授 + 物理裁判 + 仲裁官，最多 3 轮自动修正
- **Pydantic 结构化输出**：仲裁结果零解析风险
- **占位符完整性校验**：格式化后自动比对，篡改即兜底

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/你的用户名/physics-exam-generator.git
cd physics-exam-generator

# 2. 虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API Key
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
# 编辑 .env 填入真实 Key

# 5. 运行
python main.py                         # 默认 task_001.json
python main.py task_002.json           # 指定任务

# 6. 测试
pip install pytest
python -m pytest tests/ -v
```

## 输出文件

| 文件 | 说明 |
|---|---|
| `output/{id}_final.tex` | 🎯 最终 LaTeX 成品 |
| `output/{id}_draft.md` | 命题 Agent 原始草稿 |
| `output/{id}_tagged.md` | 公式隔离后的占位符文本 |
| `output/{id}_log.json` | 完整运行日志 |

## 自定义格式

编辑 `config/prompts.py` 中 `FORMATTER_SYSTEM_PROMPT` 的
`USER_CUSTOM_START` ~ `USER_CUSTOM_END` 区域。

