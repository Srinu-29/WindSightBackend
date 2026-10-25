"""Pydantic schemas for forecast accuracy analysis.

Defines response structures for statistical metrics (MAE, RMSE, Bias, Max Error),
chart-ready half-hourly series, and historical multi-day summaries.
"""
# Standard date and time representations.
from datetime import date, time
# Pydantic components for validation and documentation.
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AccuracyMetrics(BaseModel):
    """Core statistical accuracy metrics in Megawatts (MW)."""
    model_config = ConfigDict(extra='ignore')

    # Coverage counts
    matchedPeriods: int = Field(description='Number of half-hourly periods with both forecast and actual data')
    totalPeriods: int = Field(description='Total half-hourly periods evaluated')
    missingPeriods: int = Field(description='Number of periods with missing forecast or actual data')

    # Statistical error metrics (None when matchedPeriods is 0)
    maeMW: float | None = Field(default=None, description='Average Prediction Error (MAE) in MW')
    rmseMW: float | None = Field(default=None, description='Large Error Measure (RMSE) in MW')
    biasMW: float | None = Field(default=None, description='Over / Under Prediction in MW (average signed error: forecast - actual)')
    maxAbsoluteErrorMW: float | None = Field(default=None, description='Maximum absolute forecast error observed in MW')

    # Interpretative descriptions for bias
    biasDirection: str = Field(description='Over-forecasting, Under-forecasting, Unbiased, or Insufficient data')
    biasExplanation: str = Field(description='Human-readable explanation of Over / Under Prediction and operational impact')

    # Physical units and policy documentation
    units: str = Field(default='MW', description='Unit of measurement for power and error metrics')
    metricPolicyNote: str = Field(
        default='Percentage accuracy (e.g. MAPE) is excluded because wind generation frequently approaches zero MW, causing extreme division distortion. Absolute MW metrics provide reliable evaluation.',
        description='Documentation on metric selection'
    )


class ChartPeriodRecord(BaseModel):
    """Chart-ready half-hourly data record formatted for frontend visualization."""
    model_config = ConfigDict(extra='ignore')

    startTime: AwareDatetime = Field(description='Start of the half-hour period in UTC')
    settlementPeriod: int = Field(ge=1, le=50, description='Settlement period (1-48 normal, 50 max)')
    forecastMW: float | None = Field(default=None, description='Expected Wind Generation in MW')
    actualMW: float | None = Field(default=None, description='Actual generation in MW')
    signedErrorMW: float | None = Field(default=None, description='Signed error: forecast - actual in MW')
    absoluteErrorMW: float | None = Field(default=None, description='Absolute error: |forecast - actual| in MW')
    forecastPublishTime: AwareDatetime | None = Field(default=None, description='Forecast Updated At timestamp')


class SingleDateAccuracyResponse(BaseModel):
    """Evaluation response for a single purchasing decision date."""
    model_config = ConfigDict(extra='ignore')

    decisionDate: date = Field(description='Purchase Planning Date')
    deliveryDate: date = Field(description='Power Delivery Date')
    cutoffTime: time = Field(description='Purchase Cutoff Time in Europe/London')
    cutoffUtc: AwareDatetime = Field(description='Purchase Cutoff Time converted to UTC')
    timezone: str = Field(default='Europe/London', description='Local market timezone')
    metrics: AccuracyMetrics = Field(description='Aggregate statistical accuracy metrics')
    chartData: list[ChartPeriodRecord] = Field(description='Chronological chart-ready half-hour periods')



class DailyAccuracyRecord(BaseModel):
    """Summary accuracy metrics for a single day in historical analysis."""
    model_config = ConfigDict(extra='ignore')

    decisionDate: date
    deliveryDate: date
    matchedPeriods: int
    totalPeriods: int
    maeMW: float | None = None
    rmseMW: float | None = None
    biasMW: float | None = None
    maxAbsoluteErrorMW: float | None = None


class PeriodProfileRecord(BaseModel):
    """Half-hourly profile metric across the historical sample."""
    model_config = ConfigDict(extra='ignore')

    settlementPeriod: int
    sampleCount: int
    averageForecastMW: float | None = None
    averageActualMW: float | None = None
    averageMaeMW: float | None = None
    averageBiasMW: float | None = None


class HistoricalAccuracyResponse(BaseModel):
    """Aggregate historical accuracy analysis across a date range."""
    model_config = ConfigDict(extra='ignore')

    decisionDateFrom: date = Field(description='Start of decision date range')
    decisionDateTo: date = Field(description='End of decision date range')
    totalDecisionDays: int = Field(description='Number of decision days analyzed')
    cutoffTime: time = Field(description='Standard decision cutoff time')
    overallMetrics: AccuracyMetrics = Field(description='Aggregate metrics over all matched periods in the range')
    dailyBreakdown: list[DailyAccuracyRecord] = Field(description='Per-day accuracy breakdown')
    periodProfile: list[PeriodProfileRecord] = Field(description='Average performance by settlement period')
