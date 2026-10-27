from fastapi import APIRouter, Depends

from app.api import imports, projects
from app.api.deps import require_local_development

# Read-only forecast API. Unauthenticated until Milestone 2, so local development only.
router = APIRouter(prefix="/v1", dependencies=[Depends(require_local_development)])
router.include_router(projects.router)
router.include_router(imports.router)
