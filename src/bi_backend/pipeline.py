import json
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, insert, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bi_backend.config import Settings
from bi_backend.connectors import Connector
from bi_backend.errors import PipelineError
from bi_backend.models import RawRecord, SalesFact, SourceState, SyncRun, utcnow
from bi_backend.sources import SourceConfig
from bi_backend.transform import normalize


class SyncService:
    def __init__(self, sessions, settings: Settings, connector_factory: Callable | None = None):
        self.sessions = sessions
        self.settings = settings
        self.connector_factory = connector_factory or (lambda source: Connector(source, settings))

    def register(self, sources: dict[str, SourceConfig]):
        if not sources:
            return
        with self.sessions.begin() as session:
            factory = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
            statement = factory(SourceState).values([{"id": key} for key in sources])
            session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))

    def acquire(self, source_id: str) -> str:
        token = str(uuid4())
        now = utcnow()
        with self.sessions.begin() as session:
            acquired = session.execute(
                update(SourceState)
                .where(
                    SourceState.id == source_id,
                    or_(SourceState.lock_until.is_(None), SourceState.lock_until < now),
                )
                .values(
                    lock_token=token,
                    lock_until=now + timedelta(seconds=self.settings.lease_seconds),
                )
            )
            if acquired.rowcount != 1:
                raise PipelineError("source_busy", "Синхронизация источника уже выполняется", 409)

            session.execute(
                update(SyncRun)
                .where(SyncRun.source_id == source_id, SyncRun.status == "running")
                .values(
                    status="failed",
                    error_code="lease_expired",
                    error_message="Предыдущий процесс прерван",
                    finished_at=now,
                )
            )
            session.add(SyncRun(id=token, source_id=source_id, status="running"))
        return token

    def renew(self, session, source_id: str, token: str):
        now = utcnow()
        result = session.execute(
            update(SourceState)
            .where(
                SourceState.id == source_id,
                SourceState.lock_token == token,
                SourceState.lock_until >= now,
            )
            .values(lock_until=now + timedelta(seconds=self.settings.lease_seconds))
        )
        if result.rowcount != 1:
            raise PipelineError("lease_lost", "Право на запись источника истекло", 409)

    def failed(self, source_id: str, token: str, error: PipelineError):
        with self.sessions.begin() as session:
            session.execute(
                update(SyncRun)
                .where(SyncRun.id == token, SyncRun.status == "running")
                .values(
                    status="failed",
                    error_code=error.code,
                    error_message=error.message,
                    finished_at=utcnow(),
                )
            )
            session.execute(
                update(SourceState)
                .where(SourceState.id == source_id, SourceState.lock_token == token)
                .values(lock_token=None, lock_until=None)
            )

    def sync(self, source: SourceConfig) -> SyncRun:
        token = self.acquire(source.id)
        connector = None
        try:
            connector = self.connector_factory(source)
            raw_rows, facts, seen = [], [], set()
            snapshot_bytes = 0
            for page in connector.pages():
                with self.sessions.begin() as session:
                    self.renew(session, source.id, token)
                for raw in page:
                    snapshot_bytes += len(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
                    if snapshot_bytes > self.settings.max_snapshot_bytes:
                        raise PipelineError("snapshot_too_large", "Превышен размер снимка", 422)
                    if len(raw_rows) >= self.settings.max_records:
                        raise PipelineError("record_limit", "Превышен лимит записей снимка", 422)
                    row_number = len(raw_rows) + 1
                    fact = normalize(raw, source, row_number)
                    if fact["external_id"] in seen:
                        raise PipelineError("duplicate_id", "Источник вернул повторяющийся ID", 422)
                    seen.add(fact["external_id"])
                    facts.append({**fact, "run_id": token})
                    raw_rows.append({"run_id": token, "row_number": row_number, "payload": raw})
            if not facts and not source.allow_empty_snapshot:
                raise PipelineError(
                    "empty_snapshot", "Пустой снимок запрещён конфигурацией источника", 422
                )

            with self.sessions.begin() as session:
                self.renew(session, source.id, token)
                session.execute(delete(SalesFact).where(SalesFact.source_id == source.id))
                if facts:
                    session.execute(insert(RawRecord), raw_rows)
                    session.execute(insert(SalesFact), facts)
                session.execute(
                    update(SourceState)
                    .where(SourceState.id == source.id)
                    .values(lock_token=None, lock_until=None, last_success_at=utcnow())
                )
                run = session.get(SyncRun, token)
                run.status = "succeeded"
                run.records_count = len(facts)
                run.finished_at = utcnow()
            return run
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, PipelineError)
                else PipelineError("internal_error", "Внутренняя ошибка синхронизации", 500)
            )
            error.run_id = token
            self.failed(source.id, token, error)
            raise error from None
        finally:
            if connector:
                connector.close()
