from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, update

from bi_backend.errors import PipelineError
from bi_backend.models import RawRecord, SalesFact, SourceState, SyncRun, utcnow
from bi_backend.transform import normalize


def test_normalization_preserves_money_and_converts_timezone(source, raw):
    fact = normalize(raw, source, 1)
    assert fact["amount"] == Decimal("123.45")
    assert fact["occurred_at"].hour == 7
    assert fact["currency"] == "RUB"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", "-1"),
        ("amount", "0.001"),
        ("amount", True),
        ("amount", "1e20"),
        ("currency", "руб"),
        ("currency", "RURR"),
        ("date", "invalid"),
        ("id", ""),
        ("id", True),
        ("title", {}),
    ],
)
def test_invalid_fields_rejected(source, raw, field, value):
    raw[field] = value
    with pytest.raises(PipelineError) as error:
        normalize(raw, source, 1)
    assert error.value.status_code == 422


def test_repeated_sync_replaces_snapshot_without_duplicates(sync_service, source, raw, database):
    service, pages = sync_service
    pages[:] = [[raw, {**raw, "id": "2"}]]
    run1 = service.sync(source)
    pages[:] = [[{**raw, "amount": "456.78"}]]
    run2 = service.sync(source)
    _, sessions, _ = database
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SalesFact)) == 1
        fact = session.get(SalesFact, (source.id, "1"))
        assert fact.amount == Decimal("456.78")
        assert fact.run_id == run2.id
        assert session.scalar(select(func.count()).select_from(RawRecord)) == 3
        assert session.get(SyncRun, run1.id).status == "succeeded"


@pytest.mark.parametrize("failure", ["invalid", "duplicate", "remote", "empty"])
def test_failed_sync_preserves_previous_mart(sync_service, source, raw, database, failure):
    service, pages = sync_service
    pages[:] = [[raw]]
    original = service.sync(source)
    pages[:] = {
        "invalid": [[raw, {**raw, "id": "2", "amount": "oops"}]],
        "duplicate": [[raw], [raw]],
        "remote": [[raw], PipelineError("source_unavailable", "Unavailable")],
        "empty": [[]],
    }[failure]
    with pytest.raises(PipelineError) as error:
        service.sync(source)
    _, sessions, _ = database
    with sessions() as session:
        assert session.get(SalesFact, (source.id, "1")).run_id == original.id
        assert session.scalar(select(func.count()).select_from(RawRecord)) == 1
        assert session.get(SyncRun, error.value.run_id).status == "failed"
        assert session.get(SourceState, source.id).lock_token is None


def test_concurrent_source_rejected_then_expired_run_recovered(sync_service, source, database):
    service, _ = sync_service
    run_id = service.acquire(source.id)
    with pytest.raises(PipelineError) as error:
        service.acquire(source.id)
    assert error.value.status_code == 409
    _, sessions, _ = database
    with sessions.begin() as session:
        session.execute(update(SourceState).values(lock_until=utcnow() - timedelta(seconds=1)))
    replacement = service.acquire(source.id)
    with sessions() as session:
        assert session.get(SyncRun, run_id).error_code == "lease_expired"
        assert session.get(SourceState, source.id).lock_token == replacement
    with sessions.begin() as session, pytest.raises(PipelineError):
        service.renew(session, source.id, run_id)


def test_record_limit_fails_entire_snapshot(sync_service, source, raw):
    service, pages = sync_service
    service.settings.max_records = 1
    pages[:] = [[raw, {**raw, "id": "2"}]]
    with pytest.raises(PipelineError) as error:
        service.sync(source)
    assert error.value.code == "record_limit"


def test_snapshot_byte_limit_covers_extra_raw_fields(sync_service, source, raw):
    service, pages = sync_service
    service.settings.max_snapshot_bytes = 1024
    pages[:] = [[{**raw, "unmapped_payload": "x" * 1025}]]
    with pytest.raises(PipelineError) as error:
        service.sync(source)
    assert error.value.code == "snapshot_too_large"


def test_database_write_failure_rolls_back_delete_and_raw_insert(
    sync_service, source, raw, database
):
    service, pages = sync_service
    pages[:] = [[raw]]
    original = service.sync(source)
    _, sessions, engine = database

    def fail_on_fact_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO sales_facts"):
            raise RuntimeError("Simulated write failure")

    event.listen(engine, "before_cursor_execute", fail_on_fact_insert)
    try:
        with pytest.raises(PipelineError) as error:
            service.sync(source)
        assert error.value.code == "internal_error"
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_fact_insert)
    with sessions() as session:
        assert session.get(SalesFact, (source.id, "1")).run_id == original.id
        assert session.scalar(select(func.count()).select_from(RawRecord)) == 1
