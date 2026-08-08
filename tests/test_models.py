import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

from app.db.database import create_db_engine
from app.db.models import (
    AIRunRecord,
    Base,
    Capability,
    CustomerProfile,
    Experience,
    ExpertCollaboration,
    ExpertContribution,
    ExpertReply,
    IdempotencyRecord,
    Job,
    Message,
    ResearchStep,
    ResearchTask,
    ReviewRecord,
    Session,
    SolutionRun,
    Source,
    User,
)

MODELS = [
    User,
    Source,
    CustomerProfile,
    Experience,
    Capability,
    Session,
    Message,
    SolutionRun,
    Job,
    ReviewRecord,
    AIRunRecord,
    ResearchTask,
    ResearchStep,
    ExpertContribution,
    ExpertCollaboration,
    ExpertReply,
]


def test_all_core_models_use_uuid_and_soft_delete_audit_columns() -> None:
    for model in MODELS:
        table = model.__table__
        assert isinstance(table.c.id.type, UUID)
        assert {"created_at", "updated_at", "is_deleted"}.issubset(table.c.keys())
        instance = model()
        instance.id = uuid.uuid4()
        assert isinstance(instance.id, uuid.UUID)


def test_dynamic_fields_use_postgresql_jsonb() -> None:
    assert isinstance(CustomerProfile.__table__.c.profile.type, JSONB)
    assert isinstance(Experience.__table__.c.data.type, JSONB)
    assert isinstance(Capability.__table__.c.data.type, JSONB)
    assert isinstance(SolutionRun.__table__.c.profile_snapshot.type, JSONB)
    assert isinstance(SolutionRun.__table__.c.retrieval_snapshot.type, JSONB)
    assert isinstance(SolutionRun.__table__.c.result.type, JSONB)
    assert isinstance(IdempotencyRecord.__table__.c.response_data.type, JSONB)


def test_idempotency_record_has_workspace_key_uniqueness() -> None:
    constraints = {constraint.name for constraint in IdempotencyRecord.__table__.constraints}
    assert "uq_idempotency_records_workspace_key" in constraints
    assert IdempotencyRecord.__table__.c.request_hash.type.length == 64


def test_expected_relationships_configure_without_ambiguity() -> None:
    configure_mappers()

    assert set(inspect(Source).relationships.keys()) == {
        "customer_profile",
        "imported_by",
        "experience",
        "capabilities",
    }
    assert {"messages", "solution_runs"}.issubset(inspect(Session).relationships.keys())
    assert {"request_message", "response_messages"}.issubset(
        inspect(SolutionRun).relationships.keys()
    )


def test_metadata_contains_core_and_idempotency_tables() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "sources",
        "customer_profiles",
        "experiences",
        "capabilities",
        "sessions",
        "messages",
        "solution_runs",
        "jobs",
        "idempotency_records",
        "review_records",
        "ai_run_records",
        "research_tasks",
        "research_steps",
        "expert_contributions",
        "expert_collaborations",
        "expert_replies",
    }


def test_all_tables_compile_to_postgresql_ddl() -> None:
    statements = [
        str(CreateTable(table).compile(dialect=postgresql_dialect()))
        for table in Base.metadata.sorted_tables
    ]

    assert len(statements) == 17
    assert all("UUID" in statement for statement in statements)


def test_database_engine_requires_asyncpg_driver() -> None:
    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        create_db_engine("sqlite+aiosqlite:///local.db")
