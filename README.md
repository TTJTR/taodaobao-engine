# xianjintuan-engine

V2 后台与业务后端说明见 [docs/V2后端实现与联调说明.md](docs/V2后端实现与联调说明.md)，
增量接口见 [docs/openapi-v2-incremental.yaml](docs/openapi-v2-incremental.yaml)，
协作上传规则见 [docs/V2_分支上传与接力规范.md](docs/V2_分支上传与接力规范.md)。

方案演练 AI 增强见 [docs/V2_方案演练AI增强实现与交接说明.md](docs/V2_方案演练AI增强实现与交接说明.md)，
演练到展示稿接口建议见 [docs/V2_演练到展示稿增量契约提案.md](docs/V2_演练到展示稿增量契约提案.md)。

AI-driven customer solution generation engine for the Feishu AI competition.

## Backend scaffold

```text
.
├── main.py                         # Root ASGI entry point
├── openapi.yaml                    # Frontend/backend HTTP contract
├── pyproject.toml
├── app/
│   ├── main.py                     # FastAPI app factory
│   ├── api/v1/
│   │   ├── router.py               # Versioned router aggregation
│   │   └── routes/health.py        # Mounted route example
│   ├── contracts/ai.py             # Dev A / Molly AI boundary
│   ├── core/
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── exception_handlers.py
│   │   ├── idempotency.py
│   │   ├── middleware.py
│   │   └── responses.py
│   ├── db/                         # Models, repositories, migrations
│   ├── integrations/               # Feishu and model adapters
│   └── services/                   # Business services and AI Harness
└── tests/test_health.py
```

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn main:app --reload
```

V1 使用 pgvector。可先启动独立数据库并执行迁移：

```powershell
docker compose -f compose.dev.yaml up -d postgres
$env:APP_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55433/taodaobao_v1"
python -m alembic upgrade head
python scripts/verify_builtin_document_index.py
```

该命令只写入明确标记为虚构的内置验收文档，并调用真实 Embedding、pgvector
与 `RetrievalService`。缺少数据库或模型配置时输出 `skipped`，不会回退到 Mock。
详见 [运行时无Mock与内置文档索引验收](docs/运行时无Mock与内置文档索引验收.md)。

V1 增量 HTTP 契约见 `docs/openapi-v1-incremental.yaml`；根目录
`openapi.yaml` 继续作为冻结的 V0.5 前后端契约。

Swagger UI is available at `http://127.0.0.1:8000/docs` and the health endpoint
at `http://127.0.0.1:8000/api/v1/health`.

V1.1 trustworthy orchestration uses a separate recoverable worker. When not using
Docker Compose, run it alongside the API:

```bash
python -m app.cli.worker
```

The trust/presentation incremental contract is documented in
`docs/openapi-v1-incremental.yaml`; implementation and provider modes are described in
`docs/V1.1后端可信编排与演示承接实现说明.md`.

## Deployment invitation gate

The login page never contains or stores an invitation code. On a Linux deployment,
enable and rotate the gate with:

```bash
python3 scripts/configure_invitation.py --enable
```

The generated code is written to `/root/taodaobao-invitation.txt` with mode `0600`.
The administrator can share it out of band. Verification creates a signed, short-lived,
HttpOnly cookie that is consumed when OAuth starts. Inspect or disable the gate with
`--check` or `--disable`; recreate the application container after changing it.
