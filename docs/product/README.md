# 淘到宝引擎产品需求索引

本目录按产品版本保存可追溯的需求、原型与交付说明。实现与契约发生差异时，以仓库根目录 `openapi.yaml`、`app/contracts/ai.py` 以及对应版本的最新 PRD 为准。

## 版本目录

- `v0.5/`：MVP 范围、服务端 PRD、任务池、需求倒推与本地交互原型。
- `v1/`：V1 总需求、后端模块 PRD、AI 服务模块 PRD、外部展示说明与本地交互原型。
- `v2/`：情报与招标、方案演练、真实运行约束、复赛交付方案及 V2 文档索引。

## 冻结与增量契约

- HTTP 冻结契约：仓库根目录 `openapi.yaml`。
- 后端与 AI 冻结契约：`app/contracts/ai.py`，仅包含五个既定方法。
- V1/V1.1 增量契约：`docs/openapi-v1-incremental.yaml` 与 `docs/openapi-presentation-v1.1-incremental.yaml`。

历史需求目录中的接口说明用于需求追溯，不覆盖仓库中的冻结与增量契约。
