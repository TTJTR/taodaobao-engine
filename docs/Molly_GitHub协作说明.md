# Molly 对接与 GitHub 协作说明

更新时间：2026-08-06

## 1. 可直接发送到群里的同步消息

```text
@Molly 我这边网关和数据库基础设施已经同步到 GitHub：
仓库：https://github.com/wxh042/xianjintuan-engine.git
代码分支：feat/gateway
最新基线：853e7db

我这边已完成 OpenAPI 契约、SQLAlchemy/Alembic/Repository、飞书登录鉴权、
FeishuAdapter、LLMAdapter 和 Mock 黄金流程，组长可以基于这些基础设施设计 Harness。

麻烦你先把 feat/gateway 最新基线同步到 feat/ai-engine，再按
app/contracts/ai.py 的五个算子开发。检索采用两阶段调用：你的 AI 模块只提取
SearchIntent；我负责提供数据库检索接口和 Top 3 经验、Top 5 能力快照；组长负责
Harness 编排和接入。你完成 Schema 或第一个算子后在群里同步，由组长组织联调，
需要数据库、API 或适配器支持时我来配合。
```

## 2. 仓库和当前状态

仓库：

```text
https://github.com/wxh042/xianjintuan-engine.git
```

分支职责：

| 分支 | 负责人 | 用途 |
| --- | --- | --- |
| `main` | 组长维护 | 稳定演示版本，不直接开发 |
| `dev` | 全组 | 联调集成，只通过 PR 合入 |
| `feat/gateway` | Dev A | API、数据库、认证、Repository、适配器和检索接口 |
| `feat/ai-engine` | Molly | AI Schema、Prompt、算子、验证器 |

网关远端最新提交：

```text
853e7db style(auth): normalize file endings
7f72354 feat(auth): add Feishu authentication and adapter layer
80c38c0 feat(db): add async migrations and repositories
```

## 3. Molly 首次同步当前代码

如果还没有克隆仓库：

```powershell
git clone https://github.com/wxh042/xianjintuan-engine.git
cd xianjintuan-engine
git fetch origin
git switch --track origin/feat/ai-engine
git merge origin/feat/gateway
git push origin feat/ai-engine
```

如果已经克隆：

```powershell
git status
git switch feat/ai-engine
git fetch origin
git pull --ff-only origin feat/ai-engine
git merge origin/feat/gateway
git push origin feat/ai-engine
```

执行 `git merge` 前必须确保 `git status` 没有未提交修改。出现冲突时不要删除另一方代码，把冲突文件发到群里共同确认。

本次合并是让 AI 分支获得当前基础设施。后续日常开发统一从 `dev` 同步，双方通过 PR 合回 `dev`。

## 4. 已经可以百分百复用的基础设施

Molly 不需要重复实现以下内容：

| 能力 | 文件 | 使用方式 |
| --- | --- | --- |
| HTTP 契约 | `openapi.yaml` | 作为前后端字段和状态唯一基线 |
| AI 算子边界 | `app/contracts/ai.py` | 保持五个方法名和调用方向 |
| 模型供应商边界 | `app/integrations/protocols.py` | AI 逻辑只依赖 `LLMAdapter` |
| Mock LLM | `app/integrations/llm.py` | 无真实 Key 时跑测试和黄金流程 |
| 配置系统 | `app/core/config.py` | 使用 `APP_AI_MODE=mock/live` |
| 错误码和响应 | `app/core/errors.py` | 供组长的 Harness 统一转换和落库 |
| 数据库模型 | `app/db/models.py` | Dev A 负责 ORM 和检索 |
| Repository | `app/db/repositories/` | Dev A 负责资产查询和软删除过滤 |
| 登录和工作空间 | `app/api/deps.py` | 自动提供当前用户和 workspace_id |
| 飞书适配器 | `app/integrations/feishu.py` | Mock/Live 使用同一接口 |

## 5. Molly 的交付范围

建议目录：

```text
app/ai/
├── schemas/
├── prompts/
├── pipelines/
├── validators/
└── engine.py
```

需要实现五个异步算子：

```python
extract_profile(raw_text, source_ids)
extract_experience(raw_text, source_id)
extract_capabilities(raw_text, source_id)
extract_search_intent(context)
generate_solution(context, retrieval_snapshot)
```

交付顺序：

1. Pydantic Schema：`CustomerProfile`、`Experience`、`Capability`、`SearchIntent`、`RetrievalSnapshot`、`Solution`。
2. 三个资料提取算子。
3. `extract_search_intent`。
4. `generate_solution` 和引用校验。
5. Mock 与单元测试。

## 6. Harness 对接契约（组长负责实现）

调用链必须是：

```text
对话上下文
  -> Molly: extract_search_intent(context)
  -> 组长 Harness: 调用 Dev A 提供的检索接口
  -> Dev A 检索接口: 返回 Top 3 Experience + Top 5 Capability
  -> Molly: generate_solution(context, retrieval_snapshot)
  -> 组长 Harness: 编排状态和异常，调用 Dev A 持久化接口保存结果
```

边界要求：

- AI 模块不得接收全量经验库或能力库。
- AI 模块不得直接查询数据库，也不得依赖 SQLAlchemy ORM 对象。
- `retrieval_snapshot.experiences` 最多 3 条，`capabilities` 最多 5 条。
- `Solution` 必须输出八个区块，字段以 `openapi.yaml` 为准。
- 引用只能指向输入上下文或 `retrieval_snapshot` 中真实存在的来源。
- 资产之后被软删除，已保存的历史快照仍必须能够展示。
- 算子遇到 LLM 超时、限流、格式错误时直接抛标准 Python Exception。
- Molly 不捕获后伪装成成功结果；组长的 Harness 捕获异常并转换状态码，再调用持久化接口更新 `SolutionRun`。

## 7. 双方接口责任

Molly 交付给组长，并同步 Dev A：

- `app/ai/` 实现代码。
- 可直接导入的 AI Engine 实现类。
- Pydantic 输入输出 Schema。
- Mock 模式和单元测试。
- Prompt 版本或常量位置。
- 异常类型与可能触发条件说明。

Dev A 提供给组长和 Molly：

- `context` 和 `retrieval_snapshot` 的纯 Python/Pydantic 数据。
- `LLMAdapter` 实例和 `ai_mode` 配置。
- PostgreSQL Top-K 查询结果。
- PostgreSQL 检索和 `SolutionRun` 持久化接口；状态编排与异常转换由组长负责。
- HTTP 路由、鉴权、工作空间隔离和幂等控制。

## 8. 不要直接修改的目录

Molly 默认只修改 `app/ai/` 和相应测试。以下内容需要先在群里说明并由双方确认：

```text
app/api/
app/core/
app/db/
app/integrations/
app/contracts/ai.py
openapi.yaml
pyproject.toml
```

特别是不能静默修改算子名称、输入输出字段、Top-K 数量和状态枚举。

## 9. 提交和 PR 流程

每完成一个可测试单元：

```powershell
git status
git add app/ai tests
git commit -m "feat(ai): add search intent extraction"
git push origin feat/ai-engine
```

提交前执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests
git diff --check
```

创建 PR：

```text
base: dev
compare: feat/ai-engine
```

PR 描述必须包含完成内容、契约变化、测试结果、风险，以及需要组长或 Dev A 配合的事项。

## 10. 密钥与内部文档

不得提交 `.env`、飞书密钥、模型 API Key、Cookie、客户真实数据和本地数据库文件。

内部需求、分工和开发日志已由 `.gitignore` 排除，不要使用 `git add -f` 强制上传。可提交 `.env.example`，但只能保留无敏感性的示例值。

## 11. 第一次对接验收

Molly 完成首批代码后，双方按以下顺序联调：

1. 组长的 Harness 能导入 AI Engine，且 AI Engine 不依赖路由或数据库代码。
2. Mock 模式运行五个算子。
3. SearchIntent 能被序列化并用于 DB 查询。
4. 空快照及 Top 3/Top 5 快照都能生成符合 Schema 的方案。
5. 故意触发 LLM 异常，确认异常到达组长的 Harness 并落为 `failed`。
6. `pytest`、Ruff 和黄金流程全部通过后再合入 `dev`。
