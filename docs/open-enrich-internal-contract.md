# Open Enrich 内部服务通信契约

本契约用于 FastAPI 与内部 Open Enrich Adapter Service 通信，不是公网 API。Node 服务是候选情报供应商，无权访问业务 PostgreSQL，也无权修改客户画像、企业经验或企业能力。

## 作业协议

```text
POST /internal/v1/enrichment-jobs
  EnrichmentJobRequest -> 202 EnrichmentJobAccepted

GET /internal/v1/enrichment-jobs/{provider_job_id}
  -> EnrichmentJobStatus

GET /internal/v1/enrichment-jobs/{provider_job_id}/results
  -> EnrichmentJobResult（仅 completed/partial 可读取）
```

Python 权威模型位于 `app/schemas/intelligence_provider.py`。Node 包装层必须从这些模型导出的 JSON Schema 生成或校验对应类型，禁止维护语义不同的第二套手写契约。

## 请求约束

- `client_job_id`：FastAPI 生成的稳定幂等任务 ID。
- `company_name`、`website_url`：企业公开标识；显式 URL 在提交前执行公网校验。
- `allowed_fields`：最多 20 个字段；Provider 不得返回清单外字段。
- `max_tool_calls`：默认 50、最大 200，Node 服务必须硬熔断。
- `max_cost_usd`：默认 2 美元、最大 100 美元，Node 服务必须在调用外部工具前检查剩余额度。

## 结果约束

每个 `EnrichmentFact` 必须包含：

- 稳定字段名、值和情报类别；
- `provider_confidence`，范围 0–1，仅代表供应商判断；
- 至少一个 citation；
- 每个 citation 包含公开 URL 和非空原文 quote。

FastAPI 不信任 Provider 返回的引用：它会重新校验 URL，要求 DNS 可验证且全部地址为公网地址，在同一 SearchRun 的活动 RawArtifact 正文中逐字定位 quote，并自行计算 `SHA-256(quote)`。任一字段越权、URL 不安全、DNS 不可验证、制品不匹配或 quote 不存在时，整次结果拒绝落库。

通过验证后，事实以 `external_public_information` 写入 IntelligenceItem；所有实际引用制品建立 Artifact Link。随后只生成不可变 Snapshot 和 pending Profile Proposal，客户画像仍需人工确认。

## 服务安全

- 内部端点使用服务身份认证和网络策略，不依赖“仅在 VPC”作为唯一防护。
- Node 服务不接收 Workspace 数据库凭据，不直接写业务数据库。
- 禁用 Open Enrich Web 项目的 MCP、邮件、配置和通知端点。
- 日志不得记录 Bright Data/OpenRouter Key、完整个人敏感数据或内部认证头。
- Provider 超时、部分失败和预算耗尽必须返回稳定状态及错误码，不能用空结果伪装 completed。
