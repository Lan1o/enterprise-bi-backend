import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bi_backend.errors import PipelineError
from bi_backend.sources import SourceConfig


def normalize(raw: dict, source: SourceConfig, row_number: int) -> dict:
    def fail(field: str):

        raise PipelineError(
            "invalid_record", f"Запись {row_number}: недопустимое поле {field}", 422
        )

    def value(field: str, optional: bool = False):
        key = getattr(source.fields, field)
        if key is None:
            return None
        if key not in raw:
            fail(field)
        result = raw[key]
        if result is None or result == "":
            if optional:
                return None
            fail(field)
        return result

    def text(field: str, max_length: int, optional: bool = False):
        result = value(field, optional)
        if result is None:
            return None
        if isinstance(result, bool) or not isinstance(result, str | int):
            fail(field)
        result = str(result).strip()
        if not result or len(result) > max_length or "\x00" in result:
            fail(field)
        return result

    def timestamp(field: str, optional: bool = False):
        result = value(field, optional)
        if result is None:
            return None
        if not isinstance(result, str):
            fail(field)
        try:
            parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(source.source_timezone))
            return parsed.astimezone(UTC).replace(tzinfo=None)
        except (ValueError, OverflowError):
            fail(field)

    amount_raw = value("amount")
    if isinstance(amount_raw, bool) or not isinstance(amount_raw, str | int | float):
        fail("amount")
    try:
        amount = Decimal(str(amount_raw))
        if not amount.is_finite() or not 0 <= amount < Decimal("10000000000000000"):
            fail("amount")
        if amount != amount.quantize(Decimal("0.01")):
            fail("amount")
    except InvalidOperation:
        fail("amount")

    currency = text("currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        fail("currency")
    status = text("status", 100)
    closed = status in source.closed_statuses
    if source.fields.closed is not None:
        closed_raw = value("closed")
        if closed_raw in ("Y", "true", True, 1):
            closed = True
        elif closed_raw in ("N", "false", False, 0):
            closed = False
        else:
            fail("closed")

    return {
        "source_id": source.id,
        "external_id": text("external_id", 200),
        "entity_type": source.entity_type,
        "title": text("title", 500),
        "amount": amount,
        "currency": currency,
        "status": status,
        "closed": closed,
        "occurred_at": timestamp("occurred_at"),
        "updated_at": timestamp("updated_at", True),
        "customer_id": text("customer_id", 200, True),
    }
