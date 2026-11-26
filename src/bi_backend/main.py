import hmac
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from bi_backend.config import Settings
from bi_backend.db import make_database
from bi_backend.errors import PipelineError
from bi_backend.models import SalesFact, SourceState, SyncRun, utcnow
from bi_backend.pipeline import SyncService
from bi_backend.schemas import (
    Recommendation,
    RecommendationsRead,
    RunRead,
    SaleRead,
    SalesPage,
    SummaryRead,
)
from bi_backend.sources import SourceConfig, load_sources


def create_app(
    settings: Settings | None = None,
    sources: dict[str, SourceConfig] | None = None,
    connector_factory=None,
) -> FastAPI:
    settings = settings or Settings()
    sources = sources if sources is not None else load_sources(settings.sources_file)
    engine, sessions = make_database(settings.database_url)
    service = SyncService(sessions, settings, connector_factory)

    @asynccontextmanager
    async def lifespan(_):
        service.register(sources)
        yield
        engine.dispose()

    app = FastAPI(
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
        title="Enterprise BI integration API",
        version="0.1.0",
        description="Локальная загрузка 1С OData и Битрикс24 REST в аналитическую витрину.",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.sessions, app.state.sync_service = sessions, service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    @app.exception_handler(PipelineError)
    async def handle_pipeline_error(_, exc: PipelineError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "code": exc.code,
                    "message": exc.message,
                    "run_id": exc.run_id,
                }
            },
        )

    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authenticate(key: Annotated[str | None, Depends(key_header)]):
        expected = settings.api_key.get_secret_value()
        if expected and (not key or not hmac.compare_digest(key.encode(), expected.encode())):
            raise HTTPException(401, "Требуется корректный X-API-Key")

    def session_dependency():
        with sessions() as session:
            yield session

    db = Annotated[object, Depends(session_dependency)]
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @app.get("/health", tags=["health"])
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    static_path = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_path), name="static")

    @app.get("/docs", include_in_schema=False)
    def docs():
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=app.title,
            swagger_js_url="/static/swagger-ui-bundle.js",
            swagger_css_url="/static/swagger-ui.css",
            swagger_favicon_url="/static/favicon-32x32.png",
            swagger_ui_parameters={"validatorUrl": None},
        )

    @router.get("/sources", tags=["sources"])
    def list_sources(session: db):
        states = {state.id: state for state in session.scalars(select(SourceState))}
        return [
            {
                "id": source.id,
                "kind": source.kind,
                "entity_type": source.entity_type,
                "poll_seconds": source.poll_seconds,
                "last_success_at": states[source.id].last_success_at,
            }
            for source in sources.values()
        ]

    @router.post(
        "/sources/{source_id}/sync", response_model=RunRead, tags=["sources"], status_code=201
    )
    def sync_source(source_id: str):
        if source_id not in sources:
            raise HTTPException(404, "Источник не найден")
        return service.sync(sources[source_id])

    @router.get("/runs", response_model=list[RunRead], tags=["runs"])
    def list_runs(
        session: db,
        source_id: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        query = select(SyncRun)
        if source_id:
            query = query.where(SyncRun.source_id == source_id)
        return list(
            session.scalars(
                query.order_by(SyncRun.started_at.desc(), SyncRun.id).limit(limit).offset(offset)
            )
        )

    @router.get("/runs/{run_id}", response_model=RunRead, tags=["runs"])
    def get_run(run_id: str, session: db):
        run = session.get(SyncRun, run_id)
        if not run:
            raise HTTPException(404, "Запуск не найден")
        return run

    @router.get("/sales", response_model=SalesPage, tags=["analytics"])
    def list_sales(
        session: db,
        source_id: str | None = None,
        currency: str | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        filters = [
            getattr(SalesFact, key) == value
            for key, value in {
                "source_id": source_id,
                "currency": currency,
                "entity_type": entity_type,
                "status": status,
            }.items()
            if value is not None
        ]
        total = session.scalar(select(func.count()).select_from(SalesFact).where(*filters))
        items = list(
            session.scalars(
                select(SalesFact)
                .where(*filters)
                .order_by(SalesFact.source_id, SalesFact.external_id)
                .limit(limit)
                .offset(offset)
            )
        )
        return SalesPage(
            total=total,
            limit=limit,
            offset=offset,
            items=[SaleRead.model_validate(item) for item in items],
        )

    @router.get("/analytics/summary", response_model=list[SummaryRead], tags=["analytics"])
    def summary(session: db):
        groups = [SalesFact.source_id, SalesFact.entity_type, SalesFact.currency, SalesFact.status]
        return (
            session.execute(
                select(
                    *groups,
                    func.count().label("documents_count"),
                    func.sum(SalesFact.amount).label("total_amount"),
                )
                .group_by(*groups)
                .order_by(*groups)
            )
            .mappings()
            .all()
        )

    @router.get("/recommendations", response_model=RecommendationsRead, tags=["recommendations"])
    def recommendations(session: db, limit: int = Query(100, ge=1, le=1000)):
        now = utcnow()
        cutoff = now - timedelta(days=settings.stale_days)

        rows = list(
            session.scalars(
                select(SalesFact)
                .where(
                    SalesFact.entity_type == "opportunity",
                    SalesFact.closed.is_(False),
                    SalesFact.updated_at < cutoff,
                )
                .order_by(SalesFact.updated_at, SalesFact.source_id, SalesFact.external_id)
                .limit(limit + 1)
            )
        )
        return RecommendationsRead(
            truncated=len(rows) > limit,
            items=[
                Recommendation(
                    source_id=row.source_id,
                    external_id=row.external_id,
                    message=(
                        "Проверьте актуальность сделки и свяжитесь с ответственным: "
                        "давно нет изменений."
                    ),
                    days_since_update=(now - row.updated_at).days,
                    amount=row.amount,
                    currency=row.currency,
                )
                for row in rows[:limit]
            ],
        )

    app.include_router(router)
    return app


def app_factory():
    return create_app()
