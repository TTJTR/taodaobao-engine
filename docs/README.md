# 淘到宝引擎文档中心

这里是仓库文档的统一入口。阅读顺序按“运行产品 → 理解产品 → 开发验证”组织。公开作品集不保留团队分工、赛事提交、录屏和接力过程材料。

## 1. 使用与部署

- [本地部署说明](本地部署说明.md)：从空电脑启动、配置、持久化、备份恢复和降级策略。
- [本地部署故障排查](本地部署故障排查.md)：Docker、端口、登录、模型、Worker 和飞书问题。
- [本地部署实机验收（2026-09-06）](本地部署实机验收_20260906.md)：本次 Windows + Docker 的真实验收结果和未测边界。
- [生产部署说明](生产部署说明.md)：历史云端部署路径，仅供迁移参考。

## 2. 产品与 PRD

- [本地版产品 PRD](product/淘到宝引擎_本地版产品PRD.md)：当前产品基线与验收范围。
- [完整产品需求索引](product/README.md)：V0.5、V1、V2 的需求追溯入口。
- [V1 需求索引](product/v1/README_INDEX.md)：资料、画像、可信资产、检索、方案、Deep Research 与专家协作。
- [V2 产品索引](product/v2/README.md)：情报招标、方案演练和真实运行约束。
- [企业级能力边界与升级路线](企业级能力边界与升级路线.md)：能做、不能做、企业生产升级项和发布门禁。
- [真实性审计与当前能力边界](真实性审计与当前能力边界_20260815.md)：历史实现证据与 Mock 边界审计。

## 3. 开发、架构与契约

- [开发文档](开发文档.md)：后端结构和本地开发说明。
- [V2 后端实现与联调](V2后端实现与联调说明.md)：V2 模块、接口与联调入口。
- [AI 服务交接说明](AI服务交接说明.md)：AI Provider 与 Harness 说明。
- [API 路由控制台与 Mock 边界](API路由控制台与Mock边界.md)：运行路由和真实性要求。
- 冻结契约：仓库根目录 `openapi.yaml` 与 `app/contracts/ai.py`。
- 增量契约：`openapi-local-deployment-incremental.yaml`、`openapi-v1-incremental.yaml`、`openapi-v2-incremental.yaml` 和 `openapi-presentation-v1.1-incremental.yaml`。
- [项目结构与维护约定](项目结构与维护约定.md)：目录职责、文件归属和提交边界。

## 4. 验收与质量证据

- [V2 测试验收结果](V2测试验收结果.md)
- [V2 业务前端整合与验收矩阵](V2业务前端整合与验收矩阵.md)
- [运行时无 Mock 与内置文档索引验收](运行时无Mock与内置文档索引验收.md)
- `test-reports/`：专项界面和生成器报告。
- 仓库 `evals/`：各版本验收报告及真实运行记录。
- `V1_BUG_LIST.md`、`V2_BUG_LIST.md`：历史问题追踪，不等同于当前未关闭问题清单。

## 5. 求职作品集

- 开发方向：`../portfolio/developer.md`
- 产品方向：`../portfolio/product/README.md`
- 成员贡献与 GitHub 展示：`../portfolio/contributing.md`

作品集用于解释项目，不替代 PRD、契约、测试报告或个人贡献证据。

发现历史版本与当前实现冲突时，以冻结契约、数据库迁移和最新测试证据为准，不通过修改冻结契约来迁就旧文档。
