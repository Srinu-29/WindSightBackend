# A health check answers "is our backend responding?" It does not contact
# Elexon or prove that external services work. The frontend uses this route.
from fastapi import APIRouter
from app.schemas.health import HealthResponse

router = APIRouter(tags=['health'])


@router.get('/health', response_model=HealthResponse)
def health() -> HealthResponse:
    # main.py adds /api, so the public URL is /api/health.
    # "-> HealthResponse" documents the return type; response_model above makes
    # FastAPI validate/serialize the result. Returning an object sends JSON.
    return HealthResponse(status='ok', service='windsight-backend')
