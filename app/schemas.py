from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: Optional[str] = None
    status: str
    error: Optional[str] = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        # SQLite drops tzinfo on read; timestamps are always stored as UTC.
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


class FeatureOut(BaseModel):
    index: int
    geometry_type: Optional[str]
    crs: Optional[str]
    geometry: Optional[dict[str, Any]] = None
    properties: dict[str, Any]
    measurement: dict[str, Any]


class MeasurementItem(BaseModel):
    index: int
    geometry_type: Optional[str]
    crs: Optional[str]
    properties: dict[str, Any]
    measurement: dict[str, Any]


class Page(BaseModel):
    file_id: str
    crs: Optional[str]
    total: int
    limit: int
    offset: int


class FeaturePage(Page):
    results: list[FeatureOut]


class MeasurementPage(Page):
    summary: dict[str, Any]
    results: list[MeasurementItem]
