# 淘到宝引擎

企业知识驱动型售前智能决策与协作平台。系统把企业资料、客户画像、已审核历史经验和企业原子能力连接到快速方案、Deep Research、专家协作、情报与招标及方案演练，并通过来源状态、检索快照和后端 Trust Gate 约束 AI 输出。

当前仓库的 `main` 分支是 **Local First 产品候选基线**：适合本机使用、作品集展示和受控试点；尚不能直接承诺为企业级生产服务。详细边界见[企业级能力边界与升级路线](docs/企业级能力边界与升级路线.md)。

每次推送与 Pull Request 都会运行 GitHub Actions，检查代码、回归测试、Compose 配置，并确认默认镜像不包含 PyTorch、Docling 等重型本地 AI 依赖。

## 快速开始

要求：Windows 10/11、Docker Desktop（Linux containers）和 PowerShell 7。

```powershell
git clone https://github.com/TTJTR/taodaobao-engine.git
Set-Location .\taodaobao-engine
.\scripts\start-local.ps1
```

首次启动会创建未跟踪的 `.env`、生成本地安全凭据、构建镜像并自动执行数据库迁移。启动后访问：

| 入口 | 默认地址 |
|---|---|
| 产品前端 | <http://127.0.0.1:3000> |
| API | <http://127.0.0.1:8000/api/v1> |
| API 文档 | <http://127.0.0.1:8000/docs> |
| 健康检查 | <http://127.0.0.1:8000/api/v1/health> |

常用命令：

```powershell
.\scripts\start-local.ps1 -NoBuild
.\scripts\status-local.ps1
.\scripts\logs-local.ps1 -Service app -Follow
.\scripts\stop-local.ps1
```

完整安装、配置、局域网访问和备份恢复见[本地部署说明](docs/本地部署说明.md)，启动异常见[故障排查](docs/本地部署故障排查.md)。

## 当前运行边界

- PostgreSQL、Redis、上传文件和长任务状态真实持久化，容器重启不会主动清空 Volume。
- Chat 与 Embedding 分开配置，支持兼容 OpenAI Schema 的服务端 Provider。
- 默认镜像使用轻量文档解析器，不包含 PyTorch/Docling；仅在复杂 PDF/OCR 场景按需启用增强镜像。
- AI、飞书、公开情报和外部展示服务未配置时明确显示 `not_configured` 或 `disabled`，不会返回 Mock 成功。
- 示例资料明确标记 `is_demo=true`，但走与真实资料相同的导入、提取和审核流程。
- 默认只监听 `127.0.0.1`；局域网访问必须主动配置，当前方案不适合直接暴露公网。

## 可信规则

1. 历史经验与企业原子能力分开管理、分开检索。
2. 只有已审核且来源有效的资产可以参与在线检索。
3. 原文更新、删除或权限失效后，旧资产暂停检索。
4. 输出区分 `historical_fact`、`enterprise_capability`、`ai_inference` 和 `pending_confirmation`。
5. 找不到企业依据时拒绝编造；每次运行保留独立检索快照。
6. AI 的 `recommended_action` 只是建议，最终动作由后端 Trust Gate 决定。

## 仓库导航

| 目的 | 入口 |
|---|---|
| 文档总目录 | [docs/README.md](docs/README.md) |
| 当前产品 PRD | [docs/product/淘到宝引擎_本地版产品PRD.md](docs/product/淘到宝引擎_本地版产品PRD.md) |
| 历史需求索引 | [docs/product/README.md](docs/product/README.md) |
| 本地部署 | [docs/本地部署说明.md](docs/本地部署说明.md) |
| 开发与架构 | [docs/开发文档.md](docs/开发文档.md) |
| API 契约 | [openapi.yaml](openapi.yaml) 与 `docs/openapi-*-incremental.yaml` |
| 测试证据 | [evals/README.md](evals/README.md) 与 `docs/test-reports/` |
| 开发求职作品集 | [portfolio/developer.md](portfolio/developer.md) |
| 产品求职作品集 | [portfolio/product/README.md](portfolio/product/README.md) |
| 成员贡献展示 | [portfolio/contributing.md](portfolio/contributing.md) |

目录职责和维护约定见[项目结构与维护约定](docs/项目结构与维护约定.md)。

## 本地非 Docker 开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose -f compose.dev.yaml up -d postgres
$env:APP_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55433/taodaobao_v1"
python -m alembic upgrade head
uvicorn main:app --reload
```

Worker 需另开终端运行：

```powershell
python -m app.cli.worker
```

## 契约与安全

- 根目录 `openapi.yaml` 是冻结 HTTP 契约。
- `app/contracts/ai.py` 保留五个既定 `AIEngine` 方法；Embedding 是共享 Provider 能力。
- `.env`、密钥、数据库、上传文件、日志和备份均不得提交到 Git。
- 未经明确授权，不向 `main` 或原有基线分支直接推送。

本项目的作品集材料描述项目级事实。任何个人职责、实习归属和量化结果都应以真实贡献证据为准。
