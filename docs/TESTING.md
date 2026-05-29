# 测试指南

本文档说明 `tests/` 目录的职责划分、常用运行方式、测试数据组织方式和新增测试时的基本约定。它面向日常开发、回归验证和后续贡献者阅读。

## 目录职责

```text
tests/
├── test_state_machine.py   # 状态机主流程与 retry 路由
├── test_review_fixes.py    # review 修复后的关键回归场景
├── test_parser.py          # 公式、figure、标题隔离解析
├── test_merger.py          # LaTeX 回填、引用改写、小问命令生成
├── test_format.py          # 格式化输出清理与占位符保护
├── test_outputs.py         # 输出资产写入，含 TikZ 草稿
├── test_retry_context.py   # 重试上下文结构化
├── test_template_agent.py  # CPHOS 模板契约检查与自动修正
├── test_title.py           # 标题解析工具
├── test_settings.py        # LLM 设置仓储与管理路由
├── topics.py               # 测试主题数据加载辅助
└── fixtures/
    └── topics.js           # 主题池数据
```

各测试文件按“模块职责”拆分，而非按开发阶段拆分。新增功能时，优先沿着现有职责归类，避免把同一行为拆散在多个文件中。

## 常用命令

仓库使用 `pytest`。在 Unix 风格 shell 中：

```bash
PYTHONPATH=src python -m pytest -q
```

在 Windows PowerShell 中：

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
```

常用子集命令：

```bash
PYTHONPATH=src python -m pytest tests/test_state_machine.py -q
PYTHONPATH=src python -m pytest tests/test_parser.py -q
PYTHONPATH=src python -m pytest tests/test_merger.py -q
PYTHONPATH=src python -m pytest tests/test_format.py -q
PYTHONPATH=src python -m pytest tests/test_outputs.py -q
PYTHONPATH=src python -m pytest tests/test_retry_context.py -q
PYTHONPATH=src python -m pytest tests/test_template_agent.py -q
PYTHONPATH=src python -m pytest tests/test_title.py -q
PYTHONPATH=src python -m pytest tests/test_settings.py -q
```

带详细测试名输出：

```bash
PYTHONPATH=src python -m pytest -v
```

编译级检查：

```bash
python -m compileall src tests
```

## 当前测试覆盖面

| 文件 | 覆盖重点 |
|---|---|
| `test_state_machine.py` | 主工作流、首轮通过、题干重试、解答重试、统计键 |
| `test_review_fixes.py` | 仲裁组合校验、重新打标签、路由防御、命题重试清理旧解答、SDK 重试配置 |
| `test_parser.py` | block 公式、inline 公式、figure 标签、异常标签修正 |
| `test_merger.py` | 公式回填、figure 占位、引用改写、多层级小问、评分命令 |
| `test_format.py` | 小模型格式化结果清理、占位符花括号保护、代码块剥离 |
| `test_outputs.py` | assets 目录、figure 说明文件、TikZ 草稿写入 |
| `test_retry_context.py` | 命题重试、解题重试的结构化上下文 |
| `test_template_agent.py` | AI Reviewer 契约、Markdown 残留、外置图、评分点和层级一致性 |
| `test_title.py` | 多种标题格式解析与 isolate 集成 |
| `test_settings.py` | LLM 设置仓储（服务商/模型/绑定/应用设置）与管理路由 |

## Mock 使用方式

LLM 调用测试不依赖真实 API。状态机集成测试通过 `unittest.mock.patch` 替换：

- `build_client`
- `stream_chat`
- 仲裁模型 `create`

这样可以固定每个阶段的输出，并验证：

- retry 计数是否正确；
- route 是否符合预期；
- 仲裁结构化结果是否被正确处理；
- 后处理阶段是否被进入。

新增 LLM 相关测试时，应延续这一策略，避免把网络、模型稳定性和单元测试绑在一起。

## 测试数据与 fixtures

`tests/fixtures/topics.js` 是主题池数据，`tests/topics.py` 提供读取辅助。它适合：

- 主题生成相关的批量试验；
- 手动 smoke test；
- 后续扩展为参数化测试的数据来源。

当前日常单元测试不依赖大规模主题池。单元测试更适合在测试文件内部直接构造最小输入。

## 测试组织原则

当前测试组织强调两点：覆盖关键行为，控制重复与闲置内容。

1. 测试名直接描述行为，例如 `test_router_rejects_unknown_decision`。
2. 每个测试只验证一个主结论，相关断言围绕同一行为展开。
3. 同一行为只保留一组主测试；共享工具函数与集成链路需要同时覆盖时，明确区分“工具单测”和“链路集成测试”。
4. 优先构造最小 state，避免复制完整工作流状态。
5. 对于输出文件，使用 `tmp_path` 和 `monkeypatch`，不写入真实 `output/`。
6. 对全局注册表、统计表等状态，测试前后要隔离，避免跨测试污染。
7. 修复 bug 时，先补能失败的回归测试，再改实现。
8. 测试目录中不保留没有调用入口、没有文档用途的闲置辅助文件；若文件承担手工 smoke test 或主题池职责，应在文档中明确用途。

当前 `tests/topics.py` 与 `tests/fixtures/topics.js` 不参与自动化测试，它们承担主题池辅助数据角色。若后续不继续用于手工试验或参数化扩展，应迁出 `tests/` 或删除。

## 提交前检查

在提交改动前，建议至少执行：

```bash
python -m compileall src tests
PYTHONPATH=src python -m pytest -q
```

若修改集中在单个模块，可以先跑对应测试文件，再补一次全量测试。
