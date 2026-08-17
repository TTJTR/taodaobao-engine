from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    capabilities,
    experiences,
    expert_collaborations,
    feishu_resources,
    health,
    intelligence,
    jobs,
    presentations,
    profiles,
    rehearsals,
    research,
    runtime,
    sessions,
    solutions,
    sources,
    tenders,
    usage_monitor,
)

api_router = APIRouter()
api_router.include_router(auth.router, tags=["Auth"])
api_router.include_router(feishu_resources.router, prefix="/feishu", tags=["Feishu"])
api_router.include_router(health.router, tags=["Operations"])
api_router.include_router(sources.router, prefix="/sources", tags=["Sources"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(profiles.router, prefix="/customer-profiles", tags=["Profiles"])
api_router.include_router(experiences.router, prefix="/experiences", tags=["Experiences"])
api_router.include_router(capabilities.router, prefix="/capabilities", tags=["Capabilities"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])
api_router.include_router(solutions.router, prefix="/solution-runs", tags=["Solutions"])
api_router.include_router(
    presentations.references_router,
    prefix="/presentation-references",
    tags=["Presentation References"],
)
api_router.include_router(
    presentations.styles_router, prefix="/style-profiles", tags=["Style Profiles"]
)
api_router.include_router(
    presentations.presentations_router, prefix="/presentations", tags=["Presentations"]
)
api_router.include_router(research.router, prefix="/research-tasks", tags=["Deep Research"])
api_router.include_router(
    expert_collaborations.events_router,
    prefix="/expert-collaborations",
    tags=["Expert Collaboration Events"],
)
api_router.include_router(
    expert_collaborations.router,
    prefix="/expert-collaborations",
    tags=["Expert Collaboration"],
)
api_router.include_router(runtime.runtime_router, prefix="/runtime", tags=["V2 Runtime"])
api_router.include_router(
    runtime.connections_router, prefix="/model-connections", tags=["V2 Connections"]
)
api_router.include_router(intelligence.router, prefix="/intelligence", tags=["V2 Intelligence"])
api_router.include_router(
    intelligence.profile_router, prefix="/customer-profiles", tags=["V2 Profile Intelligence"]
)
api_router.include_router(tenders.router, prefix="/tenders", tags=["V2 Tenders"])
api_router.include_router(
    tenders.matrix_router, prefix="/response-matrices", tags=["V2 Response Matrices"]
)
api_router.include_router(rehearsals.router, prefix="/rehearsals", tags=["V2 Rehearsals"])
api_router.include_router(
    usage_monitor.router, prefix="/operations/api-usage", tags=["V2 Operations"]
)
