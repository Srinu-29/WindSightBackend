# Pydantic schemas describe and validate data, similar to Zod/Joi in JavaScript.
# They are not database tables. The service uses them to check external records;
# the routes use InspectionResponse to describe the JSON returned to callers.
from datetime import date, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class WindRecord(BaseModel):
    # BaseModel supplies validation and JSON conversion. Extra Elexon fields
    # are ignored so new optional upstream fields do not break our application.
    model_config = ConfigDict(extra='ignore')
    # Literal permits only these exact strings. Fields without defaults are required.
    dataset: Literal['FUELHH', 'WINDFOR']
    # startTime = delivery time; publishTime = when the record was published.
    # AwareDatetime rejects timestamps with no timezone rather than guessing one.
    startTime: AwareDatetime
    publishTime: AwareDatetime
    # Require a real number, not numeric text, boolean, null, NaN, or infinity.
    # The unit is MW (power). We do not convert to MWh (energy) or replace gaps.
    generation: float = Field(strict=True, allow_inf_nan=False)

    @field_validator('startTime', 'publishTime')
    # @ introduces a decorator: it adds behavior to the function beneath it.
    # @classmethod makes Python pass the class as the first argument (cls),
    # while field_validator tells Pydantic when to call this function.
    @classmethod
    def to_utc(cls, value):
        # This validator runs after Pydantic parses each named timestamp.
        # classmethod receives the model class as "cls"; no instance is needed.
        # astimezone preserves the instant: 01:00+01:00 becomes 00:00Z.
        return value.astimezone(timezone.utc)


class ActualWindRecord(WindRecord):
    # Inheritance reuses the shared fields/validator and adds actual-only rules.
    dataset: Literal['FUELHH']
    fuelType: Literal['WIND']
    # A settlement date is a calendar day in the GB electricity market, not a
    # timestamp. Use startTime for the exact UTC instant of the half-hour interval.
    settlementDate: date
    # A normal day has 48 half-hours; clock changes can produce 46 or 50.
    # This checks the broad valid range, not the exact period count for that date.
    settlementPeriod: int = Field(strict=True, ge=1, le=50)


class ForecastWindRecord(WindRecord):
    # Forecasts share the base fields but must identify themselves as WINDFOR.
    dataset: Literal['WINDFOR']


class InspectionResponse(BaseModel):
    # This envelope is created by WindSight. Elexon's original response is a list.
    # totalRecords counts all matches; returnedRecords counts the displayed sample;
    # truncated tells the caller whether limit left some records out of the sample.
    source: Literal['Elexon Insights API'] = 'Elexon Insights API'
    dataset: Literal['FUELHH', 'WINDFOR']
    units: Literal['MW'] = 'MW'
    totalRecords: int
    returnedRecords: int
    truncated: bool
    # list[...] means a list of items of the indicated type. Here | means either
    # record model is accepted; it is a type union, not an arithmetic operation.
    records: list[ActualWindRecord | ForecastWindRecord]
