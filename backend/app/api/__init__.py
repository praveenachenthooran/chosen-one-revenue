from fastapi import APIRouter, Depends

from app.api import edits, imports, pipeline, projects
from app.auth.deps import current_user

# Every forecast endpoint requires a signed-in, provisioned user. Row-level rules are enforced
# per endpoint (see app.auth.permissions).
router = APIRouter(prefix="/v1", dependencies=[Depends(current_user)])
router.include_router(projects.router)
router.include_router(edits.router)
router.include_router(pipeline.router)
router.include_router(imports.router)
