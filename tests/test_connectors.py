import json

import httpx
import pytest

from bi_backend.config import Settings
from bi_backend.connectors import Connector
from bi_backend.errors import PipelineError


def connector(source, handler, **settings):
    return Connector(
        source,
        Settings(allowed_source_hosts=["test.local"], **settings),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def test_onec_paginates_and_orders(source):
    offsets = []

    def handler(request):
        skip = int(request.url.params["$skip"])
        offsets.append(skip)
        assert request.url.params["$orderby"] == "id"
        assert request.url.params["$format"] == "json"
        return httpx.Response(
            200, json={"value": [{"id": n} for n in range(skip, min(skip + 2, 5))]}
        )

    client = connector(source, handler)
    try:
        assert sum(len(page) for page in client.pages()) == 5
        assert offsets == [0, 2, 4]
    finally:
        client.close()


def test_onec_follows_same_endpoint_next_link(source):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(
                200, json={"value": [{"id": 1}], "odata.nextLink": "?$skiptoken=xyz"}
            )
        assert request.url.params["$skiptoken"] == "xyz"
        return httpx.Response(200, json={"value": []})

    client = connector(source, handler)
    try:
        assert list(client.pages()) == [[{"id": 1}], []]
    finally:
        client.close()


@pytest.mark.parametrize("link", ["https://evil.test/x", "http://test.local/other", "#fragment"])
def test_onec_rejects_unsafe_next_link(source, link):
    client = connector(
        source,
        lambda _: httpx.Response(
            200,
            json={
                "value": [{"id": "1"}],
                "odata.nextLink": link,
            },
        ),
    )
    try:
        with pytest.raises(PipelineError, match="пагинация"):
            list(client.pages())
    finally:
        client.close()


def test_bitrix_uses_returned_next_not_fixed_increment(source):
    source = source.model_copy(update={"kind": "bitrix24"})
    starts = []

    def handler(request):
        body = json.loads(request.content)
        starts.append(body["start"])
        assert body["order"] == {"ID": "ASC"}
        return httpx.Response(
            200,
            json=(
                {"result": [{"ID": "1"}], "next": 50, "total": 2}
                if body["start"] == 0
                else {"result": [{"ID": "2"}], "total": 2}
            ),
        )

    client = connector(source, handler)
    try:
        assert len(list(client.pages())) == 2
        assert starts == [0, 50]
    finally:
        client.close()


@pytest.mark.parametrize(
    "body,code",
    [
        ({"error": "DENIED", "error_description": "secret-token"}, "source_api_error"),
        ({"value": None}, "invalid_response"),
        ([1, 2], "invalid_response"),
    ],
)
def test_invalid_responses_do_not_expose_payload(source, body, code):
    client = connector(source, lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(PipelineError) as error:
            list(client.pages())
        assert error.value.code == code
        assert "secret-token" not in str(error.value)
    finally:
        client.close()


def test_transient_error_retried_and_response_size_limited(source):
    calls = []

    def handler(_):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, content=b"x" * 1025)

    client = connector(source, handler, max_response_bytes=1024)
    try:
        with pytest.raises(PipelineError) as error:
            list(client.pages())
        assert error.value.code == "response_too_large"
        assert len(calls) == 3
    finally:
        client.close()


def test_redirect_is_not_followed(source):
    client = connector(
        source, lambda _: httpx.Response(302, headers={"Location": "https://evil.test"})
    )
    try:
        with pytest.raises(PipelineError) as error:
            list(client.pages())
        assert error.value.code == "source_http_error"
    finally:
        client.close()


def test_float_amount_is_parsed_losslessly(source):
    client = connector(
        source, lambda _: httpx.Response(200, content=b'{"value":[{"amount":1.01}]}')
    )
    try:
        assert list(client.pages())[0][0]["amount"] == "1.01"
    finally:
        client.close()


def test_bitrix_incomplete_snapshot_rejected(source):
    source = source.model_copy(update={"kind": "bitrix24"})
    client = connector(
        source, lambda _: httpx.Response(200, json={"result": [{"ID": 1}], "total": 2})
    )
    try:
        with pytest.raises(PipelineError) as error:
            list(client.pages())
        assert error.value.code == "incomplete_snapshot"
    finally:
        client.close()


def test_host_allowlist(source):
    with pytest.raises(PipelineError) as error:
        Connector(source, Settings(allowed_source_hosts=[]))
    assert error.value.code == "source_configuration"
