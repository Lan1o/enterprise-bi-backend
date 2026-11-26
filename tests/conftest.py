import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from bi_backend.config import Settings
from bi_backend.db import make_database
from bi_backend.pipeline import SyncService
from bi_backend.sources import FieldMap, SourceConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source():
    return SourceConfig(
        id="erp",
        kind="onec_odata",
        entity_type="sales_order",
        base_url="http://test.local/odata/",
        entity_set="Document_SalesOrder",
        page_size=2,
        fields=FieldMap(
            external_id="id",
            title="title",
            amount="amount",
            currency="currency",
            status="status",
            occurred_at="date",
            updated_at="modified",
        ),
    )


@pytest.fixture
def raw():
    return {
        "id": "1",
        "title": "Order",
        "amount": "123.45",
        "currency": "RUB",
        "status": "NEW",
        "date": "2026-01-01T12:00:00+05:00",
        "modified": "2026-01-02T12:00:00+05:00",
    }


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = os.environ.get("BI_TEST_POSTGRES_URL") or f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("BI_DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    settings = Settings(database_url=url, allowed_source_hosts=["test.local"])
    engine, sessions = make_database(url)
    yield settings, sessions, engine
    engine.dispose()
    if os.environ.get("BI_TEST_POSTGRES_URL"):
        command.downgrade(config, "base")


class FakeConnector:
    def __init__(self, pages):
        self.data = pages

    def pages(self):
        for page in self.data:
            if isinstance(page, Exception):
                raise page
            yield page

    def close(self):
        pass


@pytest.fixture
def sync_service(database, source):
    settings, sessions, _ = database
    pages = []
    service = SyncService(sessions, settings, lambda _: FakeConnector(pages))
    service.register({source.id: source})
    return service, pages
