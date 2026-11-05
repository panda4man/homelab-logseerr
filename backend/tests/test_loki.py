import httpx
import pytest
import respx

from app.search.loki import LogLine, list_containers, query_counts, query_lines

LOKI = "http://loki:3100"


@respx.mock
async def test_list_containers():
    route = respx.get(f"{LOKI}/loki/api/v1/label/container/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["plex", "sonarr"]})
    )
    assert await list_containers(LOKI, 1, 2) == ["plex", "sonarr"]
    assert route.calls[0].request.url.params["start"] == "1"


@respx.mock
async def test_list_containers_missing_data():
    respx.get(f"{LOKI}/loki/api/v1/label/container/values").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    assert await list_containers(LOKI, 1, 2) == []


@respx.mock
async def test_list_containers_http_error_propagates():
    respx.get(f"{LOKI}/loki/api/v1/label/container/values").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await list_containers(LOKI, 1, 2)


@respx.mock
async def test_query_lines_parses_streams():
    route = respx.get(f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {"stream": {"container": "plex", "level": "ERROR"}, "values": [["100", "boom"], ["99", "b2"]]},
                        {"stream": {"container": "authentik"}, "values": [["50", "hi"]]},
                    ],
                },
            },
        )
    )
    lines = await query_lines(LOKI, "{container!=\"\"}", 1, 200, 1000)
    assert lines[0] == LogLine("plex", "error", 100, "boom")
    assert lines[2] == LogLine("authentik", "", 50, "hi")
    params = route.calls[0].request.url.params
    assert params["direction"] == "backward"
    assert params["limit"] == "1000"
    assert route.call_count == 1


@respx.mock
async def test_query_lines_missing_data():
    respx.get(f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    assert await query_lines(LOKI, "q", 1, 2, 10) == []


@respx.mock
async def test_query_counts_parses_floats_and_empty_metric():
    respx.get(f"{LOKI}/loki/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"container": "plex", "level": "error"}, "value": [1, "12"]},
                        {"metric": {}, "value": [1, "3.0"]},
                    ],
                },
            },
        )
    )
    rows = await query_counts(LOKI, "q", 5)
    assert rows == [({"container": "plex", "level": "error"}, 12), ({}, 3)]


@respx.mock
async def test_query_counts_missing_data():
    respx.get(f"{LOKI}/loki/api/v1/query").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    assert await query_counts(LOKI, "q", 5) == []
