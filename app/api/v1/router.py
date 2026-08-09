from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    capabilities,
    experiences,
    expert_collaborations,
    feishu_resources,
    health,
    jobs,
    presentations,
    profiles,
    research,
    sessions,
    solutions,
    sources,
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

# Feature routers will be mounted here as their application services land:
