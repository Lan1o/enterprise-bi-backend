import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI(
    title="ERP/CRM protocol simulator",
    docs_url=None,
    redoc_url=None,
    telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
)
base_date = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def erp_rows():
    return [
        {
            "Ref_Key": f"00000000-0000-0000-0000-{i:012d}",
            "Number": f"ERP-{i:04d}",
            "Amount": f"{i * 1200}.50",
            "CurrencyCode": "RUB",
            "Status": "POSTED",
            "Date": (base_date - timedelta(days=i)).isoformat(),
            "Modified": (base_date - timedelta(days=i - 1)).isoformat(),
            "Partner_Key": f"partner-{i % 10}",
        }
        for i in range(1, 61)
    ]


def crm_rows():
    return [
        {
            "ID": str(i),
            "TITLE": f"Поставка оборудования {i}",
            "OPPORTUNITY": f"{i * 3500}.00",
            "CURRENCY_ID": "USD" if i % 7 == 0 else "RUB",
            "STAGE_ID": "WON" if i % 5 == 0 else "NEW",
            "CLOSED": "Y" if i % 5 == 0 else "N",
            "DATE_CREATE": (base_date - timedelta(days=i + 30)).isoformat(),
            "DATE_MODIFY": (base_date - timedelta(days=i)).isoformat(),
            "COMPANY_ID": str(i % 10),
        }
        for i in range(1, 126)
    ]


@app.get("/health")
def health():
    return {"status": "ok", "mode": "protocol_simulator"}


@app.get("/erp/odata/standard.odata/Document_SalesOrder")
def onec(
    credentials: Annotated[HTTPBasicCredentials, Depends(HTTPBasic())],
    top: int = Query(100, alias="$top", ge=1, le=1000),
    skip: int = Query(0, alias="$skip", ge=0),
):
    if (credentials.username, credentials.password) != (
        os.environ.get("DEMO_1C_USER", "demo"),
        os.environ.get("DEMO_1C_PASSWORD", "demo"),
    ):
        raise HTTPException(401)
    return {"value": erp_rows()[skip : skip + top]}


@app.post("/bitrix/rest/1/demo/crm.deal.list.json")
def bitrix(body: dict):
    start = body.get("start", 0)
    if type(start) is not int or start < 0:
        raise HTTPException(400)
    rows = crm_rows()
    data = {"result": rows[start : start + 50], "total": len(rows)}
    if start + 50 < len(rows):
        data["next"] = start + 50
    return data
