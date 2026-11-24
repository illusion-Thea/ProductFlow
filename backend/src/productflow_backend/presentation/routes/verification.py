from fastapi import APIRouter, HTTPException

from productflow_backend.config import get_settings

router = APIRouter(prefix="/api", tags=["verification"])


@router.get("/verification-context")
def context():
    settings = get_settings()
    if settings.app_env != "verification":
        raise HTTPException(404)
    return {
        "environment_id": settings.execution_environment_id,
        "protocol_version": 1,
        "stub_only": settings.execution_stub_only,
    }
