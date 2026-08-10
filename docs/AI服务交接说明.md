# AI 服务交接说明

## 1. 给第一次协作开发的人

后端只需要把 AI 当成一台有五个按钮的机器：

1. `extract_profile`：会议资料变成待确认客户画像。
2. `extract_experience`：项目复盘变成经验卡。
3. `extract_capabilities`：产品文档拆成原子能力卡。
4. `extract_search_intent`：客户问题变成数据库搜索单；V1 也用它做真实专家候选排序。
5. `generate_solution`：快照变成八区块方案；V1 深度研究也用它返回阶段结果。

Embedding 是 AI 内部共享工具，不是第六个跨服务按钮。AI 不读业务数据库、不拿飞书 Token、不决定审核是否通过。

## 2. 代码入口

- 五方法真实/Mock：`app/ai/engine.py`
- 冻结方法名：`app/contracts/ai.py`
- 百炼调用：`app/ai/model_client.py`
- Embedding：`app/ai/embedding.py`
- 数据结构：`app/ai/schemas/`
- V0.5 算子：`app/ai/pipelines/`
- Deep Research/专家路由：`app/ai/pipelines/deep_research.py`、`expert_routing.py`
- 飞书内容模板：`app/ai/collaboration.py`
- 回归评测：`app/ai/evaluation.py`、`evals/business_cases.json`

## 3. 后端调用顺序

```text
导入资料
  → AI 提取画像/经验/能力草稿
  → 人工审核
  → 后端保存并用 EmbeddingProvider 向量化
  → 用户提问
  → AI 生成 SearchIntent
  → 后端分开检索经验库和能力库，过滤未审核资产
  → 后端固化 RetrievalSnapshot
  → AI 生成方案
  → AI 独立质检/最多重写两次
  → 后端保存运行和报告
```

画像 Memory 单独遵守：`新会话信息 → AI 修改建议 → 人工确认 → 后端更新画像`。AI 不能跳过人工确认。

## 4. 初始化示例

离线联调不消耗模型额度：

```python
from app.ai import MockAIEngine, MockEmbeddingProvider

engine = MockAIEngine()
embedding = MockEmbeddingProvider()
```

真实百炼：

```python
from pathlib import Path
from app.ai import BailianAIEngine, BailianChatClient, BailianSettings

settings = BailianSettings.from_env(Path(".env"))
engine = BailianAIEngine(BailianChatClient(settings))
```

真实 Key 只放 `.env`，不要放代码、截图、日志或 Git。

## 5. V1 使用规则

- Deep Research：在 `SolutionContext` 设置 `mode=deep`、`research_task_id`、`trace_id` 和 `stage`，仍调用 `generate_solution`。
- 专家路由：设置 `task_type=expert_routing`，传同一 `ResearchContextPackage` 和后端真实 `candidate_records`，仍调用 `extract_search_intent`。
- 专家回答必须携带任务 ID、问题 ID、作者和飞书消息链接；进入研究时仍为 `pending_confirmation`。
- 文档、卡片、群开场只由模板整理已有结果；实际建文档、发卡片和建群由后端/飞书集成完成。

## 6. 本地验证

```bash
PYTHONPATH=. pytest -q tests/ai
ruff check app/ai app/contracts tests/ai
```

离线回归：

```bash
PYTHONPATH=. python -m app.ai.cli.evaluate_regression --mode mock
```

真实百炼回归（会产生模型费用）：

```bash
PYTHONPATH=. python -m app.ai.cli.evaluate_regression --mode bailian --env-file .env
```

Mock 的资产命中率为 0 是设计结果：它只保证格式和边界，不假装完成真实语义检索。

## 7. 还需要 Dev A 完成

1. 把 `AIEngine` 和 `EmbeddingProvider` 注入后端 Harness。
2. 保存运行元数据、审核草稿、向量、Top-K 快照和阶段结果。
3. 经验/能力分库召回，并按硬约束、问题相关、场景、来源新鲜度、向量相似度排序。
4. 实现任务重试、阶段恢复和真实飞书发送。
5. 用 Mock 先联调，再切真实百炼；不要让接口层直接拼 Prompt。

## 8. V1.1 AI 可信服务升级

- 五方法外层签名不变；快速方案传 `context.schema_version="solution-v2"` 启用可信链路。
- 后端必须同时传入 `trace_id`、`trust_deadline_seconds`、`trust_retry_budget`。
- 每条检索资产应提供 `source_snapshot`：来源版本、审核版本、权限快照、标题、更新时间和同步时间。
- AI 返回原八区块并新增 `claims`、`evidence`、`verification_summary`、`quality_attempts`、
  `recommended_action`；旧前端仍可只读八区块。
- `recommended_action` 只是 AI 信号，不是最终发布状态；后端必须自行执行 Trust Gate 并持久化
  `TrustDecision`。
- 可信链路代码位于 `app/ai/pipelines/trust.py`，版本化 Schema 位于
  `app/ai/schemas/trust.py`。
- AI 不实现后端 `RELEASE / DOWNGRADE / REVIEW / BLOCK` 状态机、不写业务数据库、
  不执行权限查询，也不把未校准不确定性包装成可信度百分比。
