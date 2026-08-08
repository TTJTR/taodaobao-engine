# xianjintuan-engine

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
docker compose up -d postgres
$env:APP_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55433/taodaobao_v1"
python -m alembic upgrade head
python -m app.cli.seed_demo --reset
```

V1 增量 HTTP 契约见 `docs/openapi-v1-incremental.yaml`；根目录
`openapi.yaml` 继续作为冻结的 V0.5 前后端契约。

Swagger UI is available at `http://127.0.0.1:8000/docs` and the health endpoint
at `http://127.0.0.1:8000/api/v1/health`.
