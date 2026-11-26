import json
import os
import time
from collections.abc import Callable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

from bi_backend.config import Settings
from bi_backend.errors import PipelineError
from bi_backend.sources import SourceConfig


class Connector:
    def __init__(
        self,
        source: SourceConfig,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.source = source
        self.settings = settings
        self.sleep = sleep
        try:
            self.base_url = source.resolve_url(settings.allowed_source_hosts)
        except ValueError as exc:
            raise PipelineError("source_configuration", str(exc), 422) from None
        auth = None
        if source.kind == "onec_odata" and source.username_env:
            username = os.environ.get(source.username_env)
            password = os.environ.get(source.password_env or "")
            if not username or not password:
                raise PipelineError("source_credentials", "Не заданы учётные данные 1С", 422)
            auth = httpx.BasicAuth(username, password)
        self.client = httpx.Client(
            timeout=settings.request_timeout,
            follow_redirects=False,
            trust_env=False,
            auth=auth,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request_json(self, method: str, url: str, **kwargs) -> dict:
        for attempt in range(3):
            try:
                with self.client.stream(method, url, **kwargs) as response:
                    if response.status_code in {429, 502, 503, 504} and attempt < 2:
                        self.sleep(0.5 * (2**attempt))
                        continue
                    if not 200 <= response.status_code < 300:
                        raise PipelineError(
                            "source_http_error",
                            f"Источник вернул HTTP {response.status_code}",
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_response_bytes:
                            raise PipelineError(
                                "response_too_large", "Ответ источника слишком большой"
                            )
                try:
                    data = json.loads(body, parse_float=str, parse_constant=self.reject_constant)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise PipelineError(
                        "invalid_json", "Источник вернул некорректный JSON"
                    ) from None
                if not isinstance(data, dict):
                    raise PipelineError("invalid_response", "Ожидался объект JSON от источника")
                if "error" in data or "error_description" in data:
                    if data.get("error") == "QUERY_LIMIT_EXCEEDED" and attempt < 2:
                        self.sleep(0.5 * (2**attempt))
                        continue
                    raise PipelineError("source_api_error", "Источник отклонил запрос API")
                return data
            except httpx.TransportError:
                if attempt == 2:
                    raise PipelineError("source_unavailable", "Источник недоступен") from None
                self.sleep(0.5 * (2**attempt))
        raise PipelineError("source_unavailable", "Лимит повторов исчерпан")

    @staticmethod
    def reject_constant(value):
        raise ValueError("Non-finite JSON number")

    @staticmethod
    def records(value) -> list[dict]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise PipelineError("invalid_response", "В ответе отсутствует массив записей")
        return value

    def pages(self) -> Iterator[list[dict]]:
        if self.source.kind == "onec_odata":
            yield from self.onec_pages()
        else:
            yield from self.bitrix_pages()

    def onec_pages(self) -> Iterator[list[dict]]:
        endpoint = self.base_url + str(self.source.entity_set)
        skip = 0
        next_url = None
        visited: set[str] = set()
        while True:
            params = (
                None
                if next_url
                else {
                    "$format": "json",
                    "$top": self.source.page_size,
                    "$skip": skip,
                    "$orderby": self.source.fields.external_id,
                }
            )
            data = self.request_json("GET", next_url or endpoint, params=params)
            items = self.records(data.get("value"))
            yield items
            skip += len(items)
            link = data.get("odata.nextLink") or data.get("@odata.nextLink")
            if link:
                if not isinstance(link, str) or not items:
                    raise PipelineError("invalid_pagination", "Некорректная пагинация 1С")
                target = urljoin(endpoint, link)
                original, candidate = urlsplit(endpoint), urlsplit(target)
                if (
                    (original.scheme, original.netloc, original.path)
                    != (candidate.scheme, candidate.netloc, candidate.path)
                    or candidate.fragment
                    or target in visited
                ):
                    raise PipelineError(
                        "invalid_pagination", "Небезопасная или циклическая пагинация"
                    )
                visited.add(target)
                next_url = target
            else:
                next_url = None
                if len(items) < self.source.page_size:
                    return

    def bitrix_pages(self) -> Iterator[list[dict]]:
        start, count = 0, 0
        selected = list(dict.fromkeys(v for v in self.source.fields.model_dump().values() if v))
        while True:
            data = self.request_json(
                "POST",
                self.base_url + "crm.deal.list.json",
                json={"start": start, "order": {"ID": "ASC"}, "select": selected},
            )
            items = self.records(data.get("result"))
            yield items
            count += len(items)
            next_start = data.get("next")
            if next_start is None:
                total = data.get("total")
                if total is not None and (type(total) is not int or total != count):
                    raise PipelineError("incomplete_snapshot", "Количество записей CRM изменилось")
                return
            if type(next_start) is not int or next_start <= start or not items:
                raise PipelineError("invalid_pagination", "Некорректная пагинация Битрикс24")
            start = next_start
