from typing import Literal
from pydantic import BaseModel


class HealthResponse(BaseModel):
    # Required fields for the health route's JSON response. Literal restricts
    # status to exactly 'ok'; service must be a string naming this backend.
    status: Literal['ok']
    service: str
