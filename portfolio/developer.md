# 开发作品集：可追溯的 AI 售前系统

[作品集导航](README.md)

## 工程问题

AI 生成结果必须绑定有效企业依据。工程挑战包括来源失效、审核状态、异步任务恢复、模型配置和接口兼容，
不是只将用户问题转发给模型。

## 架构与阅读路径

浏览器通过同源入口访问 FastAPI。API 使用 PostgreSQL/pgvector 保存业务数据及检索资产；
Durable Worker 承接持久任务。Redis 在本地部署中作为独立服务，可选 Open Enrich 使用它；核心持久任务不应被描述为 Redis 队列。
前端入口容器是 Caddy，业务静态页面仍由应用提供。

| 面试主题 | 实现证据 | 可以展开的问题 |
|---|---|---|
| 领域与隔离 | [数据模型](../app/db/models.py) | 画像版本、经验/能力、workspace 范围 |
| 检索有效性 | [检索服务](../app/services/retrieval_service.py) | 审核、来源有效性和快照 |
| 后端可信决策 | [Trust Gate](../app/services/trust_gate.py) | AI 建议与最终发布决策分离 |
| 异步流程 | [Worker](../app/services/durable_worker.py) | 任务持久化、租约与重复执行风险 |
| 冻结契约 | [HTTP](../openapi.yaml)、[AIEngine](../app/contracts/ai.py) | 五方法稳定、Embedding 共享适配 |
| 模型接入 | [Chat](../app/ai/model_client.py)、[Embedding](../app/ai/embedding.py) | 配置优先级、独立模型与维度 |
| 本地交付 | [Compose](../docker-compose.yml)、[部署说明](../docs/本地部署说明.md) | 自动迁移、卷、默认本机监听 |
| 规则验证 | [门禁测试](../tests/test_trust_gate.py)、[检索测试](../tests/test_retrieval_service.py) | 失效、缺证据和异常路径 |

## 如何复现

按[本地部署说明](../docs/本地部署说明.md)配置 Docker 与真实模型，执行 scripts/start-local.ps1。
普通电脑无需本地运行大模型；Local First 不等于完全离线，远端 AI 仍可能接收配置允许的数据。
没有 Key 时不能完成真实 AI 流程。

2026-08-28 本任务历史回归记录为 476 passed、21 skipped；本次作品集整理未重跑业务测试。
Docker 实启、备份恢复、真实模型和外部服务仍需独立验收，不以测试替身替代。

## 工程取舍与待升级

沿用模块化应用和数据库任务，先保持接口及业务规则一致。
本地 Compose 不具备主机故障接管；workspace 隔离不是完整 RBAC；卷持久化不是灾备。
企业化分析见[升级路线](../docs/企业级能力边界与升级路线.md)。

## 在个人 GitHub 展示

将自己的模块、具体提交、设计选择和验证证据置于个人主页；保留团队来源和作者记录。
当前页面是项目级工程说明，不声明任何成员独立完成整体工程。
