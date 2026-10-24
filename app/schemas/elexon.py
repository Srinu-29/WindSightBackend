from datetime import date, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class WindRecord(BaseModel):
    model_config = ConfigDict(extra='ignore')
    dataset: Literal['FUELHH', 'WINDFOR']
    startTime: AwareDatetime
    publishTime: AwareDatetime
    generation: float = Field(strict=True, allow_inf_nan=False)

    @field_validator('startTime', 'publishTime')
    @classmethod
    def to_utc(cls, value):
        return value.astimezone(timezone.utc)


class ActualWindRecord(WindRecord):
    dataset: Literal['FUELHH']
    fuelType: Literal['WIND']
    settlementDate: date
    settlementPeriod: int = Field(strict=True, ge=1, le=50)


class ForecastWindRecord(WindRecord):
    dataset: Literal['WINDFOR']


class InspectionResponse(BaseModel):
    source: Literal['Elexon Insights API'] = 'Elexon Insights API'
    dataset: Literal['FUELHH', 'WINDFOR']
    units: Literal['MW'] = 'MW'
    totalRecords: int
    returnedRecords: int
    truncated: bool
    records: list[ActualWindRecord | ForecastWindRecord]
