# 淘到宝引擎 V2 Bug List

最后更新：2026-08-14  
维护分支：`v2`

## 维护规则

本文件持续记录 V2 合并、回归和发布阶段发现的缺陷。状态只使用：`待定位`、`修复中`、`待回归`、`已关闭`、`外部依赖`。关闭缺陷必须留下修复内容和验证证据。冻结主契约 `openapi.yaml` 与 `app/contracts/ai.py` 不得因修复被破坏；V1.1/V2 新接口使用增量契约。

## 当前统计

| 状态 | 数量 |
|---|---:|
| 待定位 | 0 |
| 修复中 | 0 |
| 待回归 | 0 |
| 已关闭 | 6 |
| 外部依赖 | 1 |

## 已关闭缺陷

### BUG-V2-001：V2 与 Renderer 合并后存在两个 Alembic Head

- 严重程度：S1
- 现象：情报/招标迁移链和演示渲染迁移链没有共同 Head，生产升级无法形成确定基线。
- 修复：新增 merge revision `f2a14c8e7d90`，合并 `e5a9f3b2c018` 与 `712f72264fd8`。
- 验证：空 PostgreSQL 执行 `alembic upgrade head` 成功，随后 `alembic check` 无漂移。
- 状态：已关闭

### BUG-V2-002：Renderer 分支改写冻结主 OpenAPI 契约

- 严重程度：S1
- 现象：模板候选接口被直接写入冻结 `openapi.yaml`，违反增量契约边界。
- 修复：恢复冻结主契约，将新增路径迁移到 `docs/openapi-presentation-v1.1-incremental.yaml`。
- 验证：`git diff 8.13_backbone -- openapi.yaml app/contracts/ai.py` 无差异；契约测试通过。
- 状态：已关闭

### BUG-V2-003：合并后 Ruff 报告 9 个 UP038

- 严重程度：S2
- 现象：多个服务仍使用旧式联合类型检查，发布门禁失败。
- 修复：统一调整为 Python 3.11 支持的联合类型检查。
- 验证：`python -m ruff check app scripts tests` 通过。
- 状态：已关闭

### BUG-V2-004：Style Template 契约测试读取错误契约与参数层级

- 严重程度：S2
- 现象：测试仍从冻结主契约读取增量路径，并错误地从 Path Item 层查找 `candidate_id`。
- 修复：改读 V1.1 增量契约，并从 POST Operation 的 `parameters` 校验 UUID 参数和幂等键。
- 验证：全量测试 `381 passed, 18 skipped`。
- 状态：已关闭

### BUG-V2-005：Alembic 将 style_template_status 枚举约束误判为漂移

- 严重程度：S2
- 现象：真实迁移完成后 `alembic check` 仍建议删除由枚举生成的检查约束。
- 修复：将 `style_template_status` 纳入 Alembic 已知枚举检查约束集合。
- 验证：真实 PostgreSQL 上 `alembic check` 返回 `No new upgrade operations detected.`。
- 状态：已关闭

### BUG-V2-006：PPTX 解析器依赖 XML Element 隐式布尔值

- 严重程度：S3
- 现象：解析测试产生未来版本将改变行为的弃用告警。
- 修复：改为显式 `is None` 分支选择 transform 节点。
- 验证：全量测试中相关 21 条告警消失；仅保留第三方 Starlette/httpx2 兼容提示。
- 状态：已关闭

## 外部依赖

### EXT-V2-001：生产 Open Enrich Live 凭据尚未配置

- 类型：外部供应商凭据
- 影响：情报任务可使用内置契约 Mock 完成业务闭环，但不能抓取真实公开网络情报。
- 所需配置：Bright Data API Key、SERP Zone、Unlocker Zone、OpenRouter API Key 与服务间 Token。
- 安全边界：不得伪造或将凭据提交到 Git；缺少凭据时健康检查必须明确显示 Sidecar 未配置。
- 状态：外部依赖

