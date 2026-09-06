# API 路由控制台与 Mock 边界

## 已实现能力

“API 路由控制台”是工作区级运行配置，不是前端演示开关。

- `ai`：供应资料提取、快速方案、Deep Research、方案演练及展示规划等冻结 `AIEngine` 调用。
- `interactive-html`：供应互动 HTML 生成。
- Provider 白名单：阿里云百炼（DashScope）、DeepSeek 与自定义 OpenAI-compatible 服务。
- 自定义服务必须填写 Base URL；公网地址必须使用 HTTPS，本机或私有网络可使用 HTTP。
- 保存过程：提交候选配置 → 真实调用模型并校验 JSON → 加密保存 → 后续业务依赖读取工作区路由。
- 删除过程：删除工作区覆盖配置 → 恢复服务器默认配置。
- 页面和 API 永不返回完整 API Key，只返回末四位掩码。

Embedding 不开放直接切换。它与历史向量的模型、版本和维度绑定，必须先实现全量重建索引工作流后才能增加选择入口。

页面分为两类配置：

- 工作区 API 服务：Chat、互动 HTML 与已启用的公开情报，可在页面填写并真实测试，成功后即时生效；
- 系统集成：飞书、Embedding、数据库、Redis 与 Worker，只展示真实状态和 `.env` 配置方法，修改后必须重启。页面不会用前端假开关伪装生效。

## 真实能力规则

- API 未配置、模型无权限、额度不足或连接失败时返回失败，不生成模拟结果。
- 生产依赖注入与持久化 Worker 不引用 Mock Provider。
- `tests/` 中允许显式替身，用于验证失败路径、Schema 和 Trust Gate；这些替身不挂载为生产接口。
- `fixtures/builtin_documents/index_cases.json` 是唯一允许的内置虚构业务内容，用于真实解析、Embedding、pgvector 索引和召回验收。
- 内置资料必须标记 `is_demo=true`、使用 `builtin://` 来源并放入隔离工作区，不得进入正式客户方案。

## 增量接口

```http
GET    /api/v1/model-connections
GET    /api/v1/model-connections/catalog
POST   /api/v1/model-connections/{provider}/test
PUT    /api/v1/model-connections/{capability}/credential
DELETE /api/v1/model-connections/{capability}/credential
```

所有写请求要求 `Idempotency-Key`。冻结的根目录 `openapi.yaml` 和 `app/contracts/ai.py` 未修改；新增接口记录于 `docs/openapi-v2-incremental.yaml`。
