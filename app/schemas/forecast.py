"""Pydantic schemas for historical purchasing decision replay.

These models define data validation and JSON serialization rules for the
purchasing-decision replay feature, similar to TypeScript interfaces or Zod schemas.
"""
# date and time manage calendar dates and clock times.
from datetime import date, time
# AwareDatetime rejects naive timestamps and requires an explicit timezone (e.g. UTC 'Z').
# BaseModel provides validation, conversion, and JSON serialization.
# ConfigDict configures model behaviors (e.g. ignoring unmapped fields).
# Field adds validation boundaries (ge, le) and documentation descriptions.
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PeriodEvaluation(BaseModel):
    """Evaluation for a single half-hour settlement period on the delivery date."""
    # extra='ignore' ignores extra attributes so schema evolves without breaking clients.
    model_config = ConfigDict(extra='ignore')

    # startTime is the UTC delivery timestamp for the half hour.
    startTime: AwareDatetime = Field(description='Start of the half-hour delivery interval in UTC')
    # settlementPeriod: GB market days have periods 1 to 48 (up to 50 on autumn clock changes).
    settlementPeriod: int = Field(ge=1, le=50, description='Settlement period number (1-48 normal, up to 50 on clock change)')
    # Generation values are measured in Megawatts (MW). None indicates missing data.
    forecastGenerationMW: float | None = Field(default=None, description='Expected Wind Generation in MW (None if missing)')
    # Forecast publication timestamp in UTC returned for transparency and auditability.
    forecastPublishTime: AwareDatetime | None = Field(default=None, description='Forecast Updated At timestamp in UTC')
    # Actual metered generation from Elexon FUELHH in MW.
    actualGenerationMW: float | None = Field(default=None, description='Actual generation in MW from FUELHH (None if missing)')
    # Signed error (forecast - actual) in MW. None if either forecast or actual is missing.
    differenceMW: float | None = Field(default=None, description='Forecast minus actual in MW (None if either is missing)')


class ForecastReplayResponse(BaseModel):
    """Complete response payload for replaying purchasing decisions for a delivery date."""
    model_config = ConfigDict(extra='ignore')

    # Decision metadata
    decisionDate: date = Field(description='Purchase Planning Date')
    cutoffTime: time = Field(description='Purchase Cutoff Time in local UK time')
    cutoffUtc: AwareDatetime = Field(description='Purchase Cutoff Time converted to UTC')
    deliveryDate: date = Field(description='Power Delivery Date targeting wholesale power delivery')
    timezone: str = Field(default='Europe/London', description='Local market timezone')


    # Summary period counts
    totalPeriods: int = Field(description='Total delivery settlement periods evaluated (typically 48)')
    periodsWithForecast: int = Field(description='Count of periods with an eligible forecast published by cutoff')
    periodsWithActual: int = Field(description='Count of periods with actual generation data available')

    # Chronological half-hourly evaluation series
    periods: list[PeriodEvaluation] = Field(description='Chronological list of period evaluations')
