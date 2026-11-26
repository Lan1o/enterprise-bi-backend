from datetime import timedelta

from conftest import FakeConnector
from fastapi.testclient import TestClient
from sqlalchemy import text

from bi_backend.main import create_app
from bi_backend.models import utcnow


def test_authenticated_end_to_end_api(database, source, raw):
    settings, _, _ = database
    settings.api_key = "private-test-key"
    crm = source.model_copy(update={"id": "crm", "kind": "bitrix24", "entity_type": "opportunity"})
    old = (utcnow() - timedelta(days=60)).isoformat() + "Z"
    rows = {
        "erp": [[raw]],
        "crm": [[{**raw, "modified": old}, {**raw, "id": "2", "currency": "USD"}]],
    }
    app = create_app(settings, {"erp": source, "crm": crm}, lambda s: FakeConnector(rows[s.id]))
    headers = {"X-API-Key": "private-test-key"}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/sources").status_code == 401
        assert client.get("/api/v1/sources", headers={"X-API-Key": "wrong"}).status_code == 401
        for source_id in ["erp", "crm", "erp"]:
            result = client.post(f"/api/v1/sources/{source_id}/sync", headers=headers)
            assert result.status_code == 201, result.text
            assert result.json()["status"] == "succeeded"
        response = client.get("/api/v1/sales?limit=1&offset=1", headers=headers)
        assert response.json()["total"] == 3
        assert len(response.json()["items"]) == 1
        assert isinstance(response.json()["items"][0]["amount"], str)
        summary = client.get("/api/v1/analytics/summary", headers=headers).json()
        assert len(summary) == 3
        sources = client.get("/api/v1/sources", headers=headers).json()
        assert "base_url" not in sources[0]
        assert "private-test-key" not in str(sources)
        recommendations = client.get("/api/v1/recommendations?limit=1", headers=headers).json()
        assert recommendations["method"] == "rules_v1"
        assert recommendations["items"][0]["source_id"] == "crm"
        assert recommendations["items"][0]["days_since_update"] >= 60
        runs = client.get("/api/v1/runs?source_id=erp", headers=headers).json()
        assert len(runs) == 2
        assert client.get(f"/api/v1/runs/{runs[0]['id']}", headers=headers).status_code == 200
        assert client.post("/api/v1/sources/unknown/sync", headers=headers).status_code == 404
        assert client.get("/api/v1/sales?limit=1001", headers=headers).status_code == 422


def test_docs_use_only_local_assets(database):
    settings, _, _ = database
    with TestClient(create_app(settings, {})) as client:
        docs = client.get("/docs").text
        assert 'src="/static/swagger-ui-bundle.js"' in docs
        assert 'href="/static/swagger-ui.css"' in docs
        assert "cdn.jsdelivr.net" not in docs
        assert "fastapi.tiangolo.com" not in docs
        assert client.get("/static/swagger-ui-bundle.js").status_code == 200
        assert client.get("/openapi.json").json()["paths"]["/api/v1/sales"]


def test_api_error_is_audited_without_secret_payload(database, source, raw):
    settings, _, _ = database
    rows = [[{**raw, "amount": "SECRET-invalid-value"}]]
    app = create_app(settings, {source.id: source}, lambda _: FakeConnector(rows))
    with TestClient(app) as client:
        response = client.post("/api/v1/sources/erp/sync")
        assert response.status_code == 422
        assert "SECRET" not in response.text
        run_id = response.json()["detail"]["run_id"]
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "failed"
        assert client.get("/api/v1/sales").json()["total"] == 0


def test_migrations_create_bi_views_and_match_models(database, source, raw, sync_service):
    service, pages = sync_service
    pages[:] = [[raw]]
    service.sync(source)
    _, _, engine = database
    with engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM mart_sales_summary")).mappings().one()
        assert row["documents_count"] == 1
        assert row["currency"] == "RUB"


def test_recommendations_exclude_closed_and_missing_modification_time(database, source, raw):
    settings, _, _ = database
    source = source.model_copy(update={"entity_type": "opportunity", "closed_statuses": ["WON"]})
    raw["modified"] = (utcnow() - timedelta(days=100)).isoformat() + "Z"
    rows = [
        [
            {**raw, "status": "WON"},
            {**raw, "id": "2", "modified": None},
            {**raw, "id": "3"},
            {**raw, "id": "4"},
        ]
    ]
    app = create_app(settings, {source.id: source}, lambda _: FakeConnector(rows))
    with TestClient(app) as client:
        assert client.post("/api/v1/sources/erp/sync").status_code == 201
        data = client.get("/api/v1/recommendations?limit=1").json()
        assert data["truncated"] is True
        assert [row["external_id"] for row in data["items"]] == ["3"]
