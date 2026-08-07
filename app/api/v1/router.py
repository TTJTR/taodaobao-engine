from fastapi import APIRouter

from app.api.v1.routes import auth, health, jobs, sources

api_router = APIRouter()
api_router.include_router(auth.router, tags=["Auth"])
api_router.include_router(health.router, tags=["Operations"])
api_router.include_router(sources.router, prefix="/sources", tags=["Sources"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])

# Feature routers will be mounted here as their application services land:
# api_router.include_router(profiles.router, prefix="/customer-profiles", tags=["Profiles"])
# api_router.include_router(experiences.router, prefix="/experiences", tags=["Experiences"])
# api_router.include_router(capabilities.router, prefix="/capabilities", tags=["Capabilities"])
# api_router.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])
# api_router.include_router(solutions.router, prefix="/solution-runs", tags=["Solutions"])

