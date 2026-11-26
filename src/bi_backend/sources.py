import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str
    title: str
    amount: str
    currency: str
    status: str
    occurred_at: str
    updated_at: str | None = None
    customer_id: str | None = None
    closed: str | None = None


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")
    kind: Literal["onec_odata", "bitrix24"]
    entity_type: Literal["sales_order", "opportunity"]
    base_url: str | None = Field(default=None, repr=False)
    base_url_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    entity_set: str | None = Field(default=None, pattern=r"^[\w]+$")
    fields: FieldMap
    closed_statuses: list[str] = Field(default_factory=list)
    source_timezone: str = "Asia/Yekaterinburg"
    poll_seconds: int = Field(default=300, ge=30)
    page_size: int = Field(default=100, ge=1, le=1000)
    allow_empty_snapshot: bool = False

    @model_validator(mode="after")
    def valid_source(self):
        ZoneInfo(self.source_timezone)
        if bool(self.base_url) == bool(self.base_url_env):
            raise ValueError("Укажите ровно одно из base_url или base_url_env")
        if self.kind == "onec_odata" and not self.entity_set:
            raise ValueError("Для 1С требуется entity_set из метаданных OData")
        return self

    def resolve_url(self, allowed_hosts: list[str]) -> str:
        url = self.base_url or os.environ.get(self.base_url_env or "", "")
        try:
            parts = urlsplit(url)
            valid = (
                parts.scheme in {"http", "https"}
                and parts.hostname in allowed_hosts
                and not parts.username
                and not parts.password
                and not parts.query
                and not parts.fragment
            )
            _ = parts.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{self.id}: URL отсутствует или хост не разрешён")
        return url.rstrip("/") + "/"


def load_sources(path: Path) -> dict[str, SourceConfig]:

    load_dotenv(override=False)
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(content, dict) or set(content) != {"sources"}:
        raise ValueError("Ожидается YAML с единственным ключом sources")
    if not isinstance(content["sources"], list):
        raise ValueError("sources должен быть списком")
    sources = [SourceConfig.model_validate(item) for item in content["sources"]]
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("ID источников должны быть уникальными")

    json.dumps([s.model_dump() for s in sources])
    return {source.id: source for source in sources}
