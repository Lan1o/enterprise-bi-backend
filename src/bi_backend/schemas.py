from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_id: str
    status: Literal["running", "succeeded", "failed"]
    records_count: int
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class SaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: str
    external_id: str
    entity_type: str
    title: str
    amount: Decimal
    currency: str
    status: str
    closed: bool
    occurred_at: datetime
    updated_at: datetime | None
    customer_id: str | None


class SalesPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SaleRead]


class SummaryRead(BaseModel):
    source_id: str
    entity_type: str
    currency: str
    status: str
    documents_count: int
    total_amount: Decimal


class Recommendation(BaseModel):
    source_id: str
    external_id: str
    rule: Literal["stale_opportunity"] = "stale_opportunity"
    message: str
    days_since_update: int
    amount: Decimal
    currency: str


class RecommendationsRead(BaseModel):
    method: Literal["rules_v1"] = "rules_v1"
    items: list[Recommendation]
    truncated: bool
