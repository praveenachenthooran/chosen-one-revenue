from typing import Annotated

from fastapi import Depends, HTTPException

from app.config import Settings, get_settings

LOCAL_ENVIRONMENTS = ("development", "test")


def require_local_development(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Gate for the unauthenticated financial endpoints.

    Google SSO and row-level authorization arrive in Milestone 2. Until then these routes
    answer 404 in staging and production, so no financial data is exposed there.
    """
    if settings.app_env not in LOCAL_ENVIRONMENTS:
        raise HTTPException(status_code=404, detail="Not Found")
