import json
import secrets
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ACCESS_FILE = ROOT / ".local-access.json"
BASE = "http://127.0.0.1:3000"
COLLECTION = "Enterprise BI Demo"
DATABASE = "Enterprise warehouse (read-only)"
DASHBOARD = "Предприятие: демонстрационный стенд"


def as_items(value):
    return value if isinstance(value, list) else value["data"]


def run():
    values = dotenv_values(ROOT / ".env")
    with httpx.Client(base_url=BASE, timeout=90, trust_env=False) as client:

        def api(method, path, **kwargs):
            print(f"{method} {path}", flush=True)
            response = client.request(method, path, **kwargs)
            if not response.is_success:
                raise RuntimeError(f"Metabase HTTP {response.status_code}: {method} {path}")
            return response.json() if response.content else None

        health = api("GET", "/api/health")
        if health.get("status") != "ok":
            raise RuntimeError("Metabase is not ready")
        pending = api("GET", "/api/session/properties").get("setup-token")
        if ACCESS_FILE.exists():
            access = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
        elif pending:
            access = {
                "metabase_url": "http://localhost:3000",
                "email": "demo@local.test",
                "password": "Aa1!" + secrets.token_urlsafe(24),
                "api_url": "http://localhost:8000/docs",
                "api_key": values["BI_API_KEY"],
            }
            with ACCESS_FILE.open("x", encoding="utf-8") as file:
                json.dump(access, file, ensure_ascii=False, indent=2)
        else:
            raise RuntimeError("Existing Metabase account found; no local credentials to reuse")
        if pending:
            api(
                "POST",
                "/api/setup",
                json={
                    "token": pending,
                    "prefs": {
                        "site_name": COLLECTION,
                        "site_locale": "ru",
                        "allow_tracking": False,
                    },
                    "user": {
                        "first_name": "Demo",
                        "last_name": "Admin",
                        "email": access["email"],
                        "password": access["password"],
                    },
                },
            )
        session = api(
            "POST",
            "/api/session",
            json={
                "username": access["email"],
                "password": access["password"],
            },
        )
        client.headers["X-Metabase-Session"] = session["id"]

        databases = as_items(api("GET", "/api/database"))
        database = next((db for db in databases if db["name"] == DATABASE), None)
        if database is None:
            database = api(
                "POST",
                "/api/database",
                json={
                    "name": DATABASE,
                    "engine": "postgres",
                    "is_full_sync": True,
                    "details": {
                        "host": "warehouse",
                        "port": 5432,
                        "dbname": "warehouse",
                        "user": "bi_reader",
                        "password": values["BI_READER_PASSWORD"],
                        "ssl": False,
                    },
                },
            )
        collection = next(
            (
                item
                for item in as_items(api("GET", "/api/collection"))
                if item["name"] == COLLECTION
            ),
            None,
        )
        if collection is None:
            collection = api(
                "POST",
                "/api/collection",
                json={
                    "name": COLLECTION,
                    "description": "Managed demo for enterprise-bi-backend",
                },
            )
        collection_id = collection["id"]
        existing = as_items(api("GET", f"/api/collection/{collection_id}/items"))

        cards = []
        for definition in card_definitions():
            card = next(
                (
                    item
                    for item in existing
                    if item.get("model") == "card" and item["name"] == definition["name"]
                ),
                None,
            )
            if card is None:
                card = api(
                    "POST",
                    "/api/card",
                    json={
                        "name": definition["name"],
                        "display": definition["display"],
                        "description": (
                            "Синтетические данные. Создано scripts/bootstrap_metabase.py."
                        ),
                        "collection_id": collection_id,
                        "visualization_settings": definition.get("visualization_settings", {}),
                        "dataset_query": {
                            "lib/type": "mbql/query",
                            "database": database["id"],
                            "stages": [
                                {"lib/type": "mbql.stage/native", "native": definition["sql"]}
                            ],
                        },
                    },
                )
            query = api("POST", f"/api/card/{card['id']}/query", json={})
            if query.get("status") != "completed":
                raise RuntimeError(f"Card {card['id']} query did not complete")
            rows = query["data"]["rows"]
            if "expected" in definition and rows != [[definition["expected"]]]:
                raise RuntimeError(f"Unexpected count in card {card['id']}")
            cards.append({**definition, "id": card["id"]})

        dashboard = next(
            (
                item
                for item in existing
                if item.get("model") == "dashboard" and item["name"] == DASHBOARD
            ),
            None,
        )
        if dashboard is None:
            dashboard = api(
                "POST",
                "/api/dashboard",
                json={
                    "name": DASHBOARD,
                    "collection_id": collection_id,
                    "description": (
                        "Демоданные ERP/CRM. Суммы заказов и сделок разделены; "
                        "заказы не являются признанной выручкой. "
                        "Рекомендации построены на правилах."
                    ),
                },
            )
        current = api("GET", f"/api/dashboard/{dashboard['id']}")
        dashcards = current.get("dashcards", [])
        present = {item.get("card_id") for item in dashcards}
        additions = []
        for index, card in enumerate(cards, 1):
            if card["id"] not in present:
                additions.append(
                    {
                        "id": -index,
                        "card_id": card["id"],
                        "row": card["row"],
                        "col": card["col"],
                        "size_x": card["width"],
                        "size_y": card["height"],
                        "parameter_mappings": [],
                    }
                )
        if additions:
            api(
                "PUT",
                f"/api/dashboard/{dashboard['id']}",
                json={
                    "dashcards": dashcards + additions,
                },
            )
        access["dashboard_url"] = f"http://localhost:3000/dashboard/{dashboard['id']}"
        ACCESS_FILE.write_text(json.dumps(access, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "dashboard_url": access["dashboard_url"],
                    "verified_cards": len(cards),
                    "credentials_file": str(ACCESS_FILE),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def card_definitions():
    return [
        {
            "name": "Заказы ERP",
            "display": "scalar",
            "row": 0,
            "col": 0,
            "width": 12,
            "height": 3,
            "expected": 60,
            "sql": (
                "SELECT COUNT(*) AS orders FROM mart_sales_documents "
                "WHERE entity_type='sales_order'"
            ),
        },
        {
            "name": "Сделки CRM",
            "display": "scalar",
            "row": 0,
            "col": 12,
            "width": 12,
            "height": 3,
            "expected": 125,
            "sql": (
                "SELECT COUNT(*) AS deals FROM mart_sales_documents WHERE entity_type='opportunity'"
            ),
        },
        {
            "name": "Заказы по месяцам, RUB",
            "display": "line",
            "row": 3,
            "col": 0,
            "width": 12,
            "height": 7,
            "visualization_settings": {
                "graph.dimensions": ["month"],
                "graph.metrics": ["orders_amount"],
            },
            "sql": """SELECT date_trunc('month', occurred_at) AS month, SUM(amount) AS orders_amount
                   FROM mart_sales_documents WHERE entity_type='sales_order' AND currency='RUB'
                   GROUP BY month ORDER BY month""",
        },
        {
            "name": "Количество сделок по стадиям",
            "display": "bar",
            "row": 3,
            "col": 12,
            "width": 12,
            "height": 7,
            "visualization_settings": {"graph.dimensions": ["status"], "graph.metrics": ["deals"]},
            "sql": """SELECT status, COUNT(*) AS deals FROM mart_sales_documents
                   WHERE entity_type='opportunity' GROUP BY status ORDER BY status""",
        },
        {
            "name": "Рекомендации: проверить сделки без изменений более 30 дней",
            "display": "table",
            "row": 10,
            "col": 0,
            "width": 24,
            "height": 8,
            "sql": """SELECT source_id, external_id, title, amount, currency,
                     EXTRACT(DAY FROM (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - updated_at)::integer
                       AS days_since_update
                   FROM mart_sales_documents WHERE entity_type='opportunity' AND closed=false
                     AND updated_at < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '30 days'
                   ORDER BY updated_at, source_id, external_id""",
        },
        {
            "name": "Суммы по источникам, типам документов и валютам",
            "display": "table",
            "row": 18,
            "col": 0,
            "width": 24,
            "height": 6,
            "sql": (
                "SELECT * FROM mart_sales_summary ORDER BY source_id, entity_type, currency, status"
            ),
        },
    ]


if __name__ == "__main__":
    run()
