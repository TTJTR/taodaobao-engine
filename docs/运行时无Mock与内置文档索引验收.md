# 运行时无 Mock 与内置文档索引验收

## 规则

- 生产运行模式只允许 `live`，不再接受 `mock` 配置值。
- AI、Embedding、飞书、公开情报、展示解析或互动 HTML 未配置时，返回 `not_configured`、HTTP 503 或把异步任务标记为失败；不得生成模拟成功结果。
- 自动化测试仍可在 `tests/` 中显式注入替身，但生产依赖注入和 Worker 不会引用这些替身。
- 唯一允许的虚构业务内容是 `fixtures/builtin_documents/index_cases.json` 中明确标注的内置测试文档。

## 内置文档验收链路

```text
synthetic fixture 文档
  → PostgreSQL Source
  → 人工审核状态与真实 Bailian Embedding
  → pgvector 索引
  → RetrievalService
  → source_snapshot / match_reasons / top_title
  → .local/builtin-index-report.json
```

运行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_builtin_document_index.py
```

环境必须提供：

- `APP_DATABASE_URL`：迁移到 Alembic Head 的 PostgreSQL + pgvector；
- `DASHSCOPE_API_KEY`：真实百炼 Embedding；
- `EMBEDDING_MODEL=text-embedding-v4`；
- `EMBEDDING_DIMENSION=1024`。

缺少真实依赖时报告为 `skipped`，不会回退到哈希向量、零向量或固定检索结果。真实调用失败时报告为 `failed`。验收通过要求四个查询的第一条结果均命中预期文档，并输出语义相似度与完整 `source_snapshot`。

## 内置文档边界

内置资料中的公司、项目、产品、数字和结论全部为虚构测试信息。资料使用 `builtin://index-fixture/...` 来源地址并标记 `is_demo=true`，不得在正式客户方案中当作企业事实或企业能力使用。正式环境如需运行，应使用隔离的演示 workspace。
