# Molly GitHub 协作说明

## 1. 仓库与分支

仓库地址：

```text
https://github.com/wxh042/xianjintuan-engine.git
```

分支职责：

| 分支 | 用途 | 是否直接开发 |
| --- | --- | --- |
| `main` | 稳定演示版本 | 禁止直接开发和推送 |
| `dev` | 两人每日集成和联调 | 禁止直接开发，使用 PR 合入 |
| `feat/gateway` | Dev A 的 API、数据库、飞书和任务开发 | Dev A 使用 |
| `feat/ai-engine` | Molly 的 AI Schema、Prompt、算子和质检开发 | Molly 使用 |

任何人都不要在 `main` 或 `dev` 上直接写代码。功能必须在个人分支完成，通过 Pull Request 合入。

## 2. 开始前的必要条件

当前最新代码在 `feat/gateway`，远程 `dev` 仍是初始版本。因此首次协作必须按以下顺序：

1. Dev A 在 GitHub 创建 PR：`feat/gateway -> dev`。
2. 检查测试和文件变化后合并该 PR。
3. Molly 确认 `dev` 已包含 `openapi.yaml`、`app/contracts/ai.py`、`app/core/` 和 `app/db/`。
4. Molly 从最新 `dev` 创建自己的 `feat/ai-engine`。

Molly 不要从旧 `main` 或旧 `dev` 提前创建分支，否则会缺少最新契约和脚手架。

## 3. 仓库邀请

Dev A 在 GitHub 仓库中执行：

1. 打开仓库页面。
2. 进入 `Settings`。
3. 进入 `Collaborators`。
4. 点击 `Add people`。
5. 输入 Molly 的 GitHub 用户名或邮箱并发送邀请。

Molly 登录 GitHub，在通知或邮件中接受邀请。私有仓库必须接受邀请后才能克隆和推送。

## 4. Molly 首次克隆

在 Molly 自己的 PowerShell 中执行：

```powershell
git clone https://github.com/wxh042/xianjintuan-engine.git
cd xianjintuan-engine
git fetch origin
git switch dev
git pull --ff-only origin dev
git switch -c feat/ai-engine
git push -u origin feat/ai-engine
```

确认当前分支：

```powershell
git branch --show-current
```

必须输出：

```text
feat/ai-engine
```

确认最新基线文件存在：

```powershell
git log --oneline -3
Get-ChildItem app\contracts,app\core,app\db
```

## 5. Molly 的代码范围

Molly 主要修改：

```text
app/ai/
├── schemas/
├── prompts/
├── pipelines/
├── retrieval/
└── validators/
```

主要交付：

- CustomerProfile、Experience、Capability、SearchIntent、RetrievalSnapshot、Solution 的 Pydantic Schema。
- 客户画像、经验和能力提取算子。
- `extract_search_intent(context)`。
- `generate_solution(context, retrieval_snapshot)`。
- Prompt、Mock、引用检查和防编造测试。

Molly 不直接修改：

```text
app/api/
app/core/
app/db/
app/integrations/
openapi.yaml
```

如果 AI 开发需要这些模块变化，先在群里说明需求，由对应负责人修改或共同评审。

## 6. 共享契约的修改规则

以下文件属于共享边界：

```text
app/contracts/ai.py
openapi.yaml
pyproject.toml
docs/开发文档.md
```

修改共享文件前必须：

1. 在群里说明修改原因、字段变化和影响范围。
2. 双方确认后再修改。
3. 在同一个 PR 中同步测试和开发文档。
4. 禁止一方静默改函数名称、输入输出字段或状态枚举。

AI 内部契约的目标调用链：

```text
extract_search_intent(context)
  -> Dev A 使用 SearchIntent 查询 PostgreSQL
  -> Dev A 固化 Top 3 Experience + Top 5 Capability
  -> generate_solution(context, retrieval_snapshot)
```

Molly 的 AI 算子不得接收全量资产库，也不得直接访问数据库 ORM 对象。

## 7. 每日开发流程

每天开始开发前，Molly 在项目目录执行：

```powershell
git switch feat/ai-engine
git fetch origin
git merge origin/dev
```

如果提示 `Already up to date.`，说明已经同步。

开发完成一小块可验证功能后：

```powershell
git status
git add app/ai tests docs/开发文档.md
git commit -m "feat(ai): add customer profile extraction schema"
git push origin feat/ai-engine
```

不要长期积累大量未提交修改。建议每个独立、可测试功能一个提交。

## 8. 提交信息规范

使用 Conventional Commits：

```text
feat(ai): add search intent extraction
fix(ai): reject unsupported source references
test(ai): cover empty retrieval snapshot
docs(ai): document solution generation contract
refactor(ai): isolate model adapter
```

禁止使用无法判断内容的提交信息：

```text
update
修改
test
123
```

## 9. 开发文档要求

每次任务开始前，在 `docs/开发文档.md` 增加一条进行中记录。任务完成后补充：

- 目标和需求依据。
- 操作步骤。
- 修改文件。
- 接口或架构决定。
- 测试命令和真实结果。
- 未完成内容及风险。

禁止只提交代码而不更新开发文档。

## 10. Molly 本地验证

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

提交前至少执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests
git diff --check
```

测试失败时不要创建合并 PR。若失败来自尚未合并的外部依赖，在 PR 中明确说明阻塞原因和复现命令。

## 11. 创建 Pull Request

功能完成并推送后，在 GitHub 创建：

```text
base: dev
compare: feat/ai-engine
```

PR 描述至少包含：

```markdown
## 完成内容
- ...

## 契约变化
- 无 / 具体字段变化

## 验证
- pytest: ... passed
- ruff: passed

## 风险与未完成
- ...
```

Dev A 检查接口兼容性和测试，Molly检查 AI 逻辑。PR 合入 `dev` 后，双方都要把最新 `dev` 合回个人分支。

## 12. PR 合并后的同步

Molly 执行：

```powershell
git switch dev
git pull --ff-only origin dev
git switch feat/ai-engine
git merge dev
git push origin feat/ai-engine
```

Dev A 在自己的 `feat/gateway` 执行同样流程。

## 13. 冲突处理

出现冲突时不要删除文件或使用 `git reset --hard`。执行：

```powershell
git status
```

根据冲突文件处理：

- `app/ai/`：Molly 主处理。
- `app/api/`、`app/core/`、`app/db/`：Dev A 主处理。
- `app/contracts/ai.py`、`openapi.yaml`、`pyproject.toml`：双方一起确认后处理。

处理完成后：

```powershell
git add 冲突文件路径
git commit
git push
```

无法判断某段代码归属时先停止合并，在群里确认，不要猜测删除另一方实现。

## 14. 密钥和数据安全

不得提交：

- `.env`
- 飞书 App Secret
- 飞书访问令牌和 Cookie
- 模型 API Key
- 客户真实敏感资料
- `.venv/`

可提交 `.env.example`，但只能包含变量名和无敏感性的示例值。

## 15. 简化版每日口诀

```text
开始：切到个人分支 -> fetch -> merge origin/dev
开发：只改负责目录 -> 写测试 -> 更新开发文档
完成：pytest -> ruff -> commit -> push
联调：个人分支 PR 到 dev -> 审查 -> 合并
同步：拉取 dev -> 合回个人分支
```

