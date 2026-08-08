# 淘到宝引擎协作开发与 Git 提交规范

当前版本已经实现什么、尚未实现什么，以及后续如何继续开发，见[《V1 集成基线接力开发说明》](V1集成基线接力开发说明.md)。

面向合作伙伴的日常操作步骤见[《合作伙伴代码更新操作手册（GitHub 网页版）》](合作伙伴代码更新操作手册.md)。本文主要说明项目规则，操作手册主要说明每一步具体怎么做。

## 1. 适用范围

本文适用于淘到宝引擎 V0.5、V1 及后续版本的产品、前端、后端、AI 服务和飞书集成协作。团队采用轻量分支模式：稳定分支只接收经过评审和验证的代码，开发人员在独立功能分支工作，通过 Pull Request 合并。

## 2. 分支结构

| 分支 | 用途 | 规则 |
|---|---|---|
| `main` | 已验收、可演示或可部署版本 | 禁止直接开发和直接 push |
| `v1-integration` | V1 集成、联调和验收 | 只通过 Pull Request 合并 |
| `feat/<module>-<feature>` | 新功能 | 一个分支只处理一个明确模块或交付目标 |
| `fix/<module>-<issue>` | 缺陷修复 | 必须包含复现步骤和回归结果 |
| `docs/<topic>` | 独立文档变更 | 不混入业务代码 |

示例：

```text
main
└── v1-integration
    ├── feat/ai-acceptance-fixes
    ├── feat/feishu-oauth
    ├── feat/feishu-document-sync
    ├── feat/expert-collaboration
    └── fix/research-task-resume
```

## 3. 开始开发

1. 明确本次产品模块、后端模块、AI 模块、上游依赖和下游依赖。
2. 完整读取总需求、对应模块 PRD、冻结契约及当前实现。
3. 检查当前分支和工作树，不能覆盖其他成员未提交的代码。
4. 从最新集成分支创建独立功能分支：

```powershell
git fetch origin
git switch v1-integration
git pull --ff-only origin v1-integration
git switch -c feat/<module>-<feature>
```

如果本地存在未提交修改，应先确认修改归属，不得使用 `git reset --hard` 或强制切换清除他人成果。

## 4. 模块边界与冻结契约

### 4.1 HTTP 契约

- 前端与后端以 `openapi.yaml` 为冻结契约。
- 不得擅自修改已有路径、字段语义或状态枚举。
- V1 新接口使用增量契约，并同步模块 PRD。

### 4.2 AI 契约

后端与 AI 服务保持以下五个冻结方法签名：

- `extract_profile`
- `extract_experience`
- `extract_capabilities`
- `extract_search_intent`
- `generate_solution`

Embedding 是共享适配能力，不增加第六个冻结方法。修改 Prompt、Schema 或归一化逻辑后，必须回归五个方法和黄金案例。

### 4.3 数据可信边界

- 经验和原子能力分开管理、分开检索。
- 只有已校验且来源有效的资产可以在线召回。
- 输出必须区分 `historical_fact`、`enterprise_capability`、`ai_inference` 和 `pending_confirmation`。
- 没有企业依据时必须拒绝编造。
- 每次方案运行保存独立检索快照。

## 5. 编码与提交规则

1. 优先复用现有结构，不进行无关重构。
2. 不把前端演示数据写成真实后端逻辑。
3. Mock 与真实接口使用相同 Schema，并明确标注 Mock。
4. 密钥只保存在被 Git 忽略的本地 `.env`，不得写入代码、日志、报告或聊天记录。
5. 不使用 `git add .` 提交来源不明的混合修改；先检查，再按文件暂存。
6. 提交信息采用 `<type>(<scope>): <summary>`：

```text
feat(feishu): add document synchronization
fix(ai): normalize experience evidence status
test(research): cover expert resume workflow
docs(collaboration): add Git workflow
```

提交前执行：

```powershell
git status --short
git diff --check
git diff --cached --stat
git diff --cached
```

## 6. 测试要求

每个 Pull Request 至少包含与改动相关的测试。AI 服务参考命令：

```powershell
$env:PYTHONPATH='.'
uv run pytest -q tests\ai
uv run ruff check app\ai tests\ai
uv run python -m app.ai.cli.evaluate_regression `
  --mode mock `
  --output evals\latest_mock_report.json
```

涉及模拟数据时运行数据集校验；涉及真实模型时记录模型、Prompt、Schema、参数版本和事实审计结果。涉及飞书时分别验证登录、权限、读取、机器人动作、回调和失败恢复。

## 7. Pull Request 流程

功能分支完成后：

```powershell
git push -u origin feat/<module>-<feature>
```

在 GitHub 创建 Pull Request，目标先选择 `v1-integration`。PR 必须说明：

- 产品目标和实现范围；
- 修改文件及核心实现；
- 上游和下游依赖；
- 测试命令和结果；
- 是否影响冻结契约；
- 数据库迁移、环境变量和部署要求；
- 未完成项、风险和回滚方式。

至少由另一名成员检查后合并。V1 集成分支完成端到端验收后，再通过 PR 合并到 `main`。

## 8. 冲突处理

1. 先同步远程目标分支并查看冲突文件。
2. 合并冲突时以冻结契约和最新确认的 V1 PRD 为准。
3. 无法判断修改归属或字段语义时暂停合并，联系对应作者确认。
4. 禁止通过整文件覆盖来快速解决冲突。
5. 解决冲突后重新运行受影响测试和契约检查。

## 9. 交接模板

```markdown
### 本次实现
- 模块：
- 功能：
- 分支：
- 提交：

### 依赖
- 上游：
- 下游：
- 环境变量/数据库迁移：

### 验证
- 执行命令：
- 测试结果：
- 人工验收：

### 契约
- openapi.yaml：未修改 / V1 增量修改
- AI 五方法签名：未修改 / 需要评审

### 未完成与风险
- 未完成项：
- 已知风险：
- 建议下一步：
```

## 10. 当前 V1 推荐协作方式

当前阶段建议把已合并并通过局部验收的成果推送到独立集成候选分支，合作伙伴从该远程分支拉取后，再为飞书 OAuth、文档同步、专家协作和生产部署分别创建功能分支。完成真实飞书端到端验收前，不直接合并到 `main`。
