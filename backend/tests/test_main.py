import json

import httpx
import pytest
import respx

LOKI = "http://localhost:3100"
OLLAMA = "http://localhost:11434"
LABELS = f"{LOKI}/loki/api/v1/label/container/values"
RANGE = f"{LOKI}/loki/api/v1/query_range"
INSTANT = f"{LOKI}/loki/api/v1/query"
GEN = f"{OLLAMA}/api/generate"


@pytest.fixture
async def client():
    import app.main as main_module

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_module.app), base_url="http://test"
    ) as c:
        yield c


def streams(*items):
    """items: (container, level, ts_ns, text)"""
    out = []
    for c, lvl, ts, text in items:
        labels = {"container": c}
        if lvl:
            labels["level"] = lvl
        out.append({"stream": labels, "values": [[str(ts), text]]})
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": out}})


EMPTY = httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": []}})


def mock_ollama(intent: dict | None, answer: str | None = "the answer"):
    """Intent calls (format=json) and answer calls share /api/generate."""
    def handler(request):
        body = json.loads(request.content)
        if body.get("format") == "json":
            if intent is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"response": json.dumps(intent)})
        if answer is None:
            return httpx.Response(503)
        return httpx.Response(200, json={"response": answer})

    route = respx.post(GEN).mock(side_effect=handler)
    return route


def answer_prompt(route):
    for call in route.calls:
        body = json.loads(call.request.content)
        if body.get("format") != "json":
            return body["prompt"]
    return None


INTENT = {"containers": ["plex"], "hours": None, "severity": "errors", "keywords": []}


@respx.mock
async def test_health_ok(client):
    respx.get(f"{LOKI}/loki/api/v1/labels").mock(return_value=httpx.Response(200, text="ready"))
    respx.get(f"{OLLAMA}/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "loki": "ok", "llm": "ok"}


@respx.mock
async def test_health_degraded(client):
    respx.get(f"{LOKI}/loki/api/v1/labels").mock(return_value=httpx.Response(503))
    respx.get(f"{OLLAMA}/api/tags").mock(return_value=httpx.Response(200, json={}))
    resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "loki": "unavailable", "llm": "ok"}

    respx.get(f"{LOKI}/loki/api/v1/labels").mock(return_value=httpx.Response(200))
    respx.get(f"{OLLAMA}/api/tags").mock(return_value=httpx.Response(500))
    resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["llm"] == "unavailable" and resp.json()["loki"] == "ok"


@respx.mock
async def test_logs_happy_path_shape(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex", "sonarr"]}))
    respx.get(RANGE).mock(side_effect=[
        streams(("plex", "error", 1_793_826_000_000_000_000, "stream stalled")),
        EMPTY,
    ])
    route = mock_ollama(INTENT, "Plex stalled.")
    resp = await client.post("/search", json={"query": "did plex have issues?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Plex stalled."
    assert body["answer_status"] == "ok"
    assert len(body["sources"]) == 1
    src = body["sources"][0]
    assert src["service"] == "plex"
    assert "stream stalled" in src["log_text"]
    assert src["levels"] == ["error"]
    assert "time_range" in src and "score" not in src
    assert "containers=[plex]" in answer_prompt(route)
    assert "severity=errors" in answer_prompt(route)
    assert "Do NOT say that no errors" in answer_prompt(route)
    assert "does NOT mean the service is healthy" not in answer_prompt(route)


@respx.mock
async def test_ladder_drops_keywords_then_severity(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    # attempt a: 2 queries empty; b: 2 queries empty; c: 1 query with data
    range_route = respx.get(RANGE).mock(side_effect=[
        EMPTY, EMPTY, EMPTY, EMPTY,
        streams(("plex", "info", 1_793_826_000_000_000_000, "just chatting")),
    ])
    route = mock_ollama({**INTENT, "keywords": ["zzz"]})
    resp = await client.post("/search", json={"query": "did plex have zzz?"})
    body = resp.json()
    assert body["answer_status"] == "ok"
    assert range_route.call_count == 5
    queries = [c.request.url.params["query"] for c in range_route.calls]
    assert "zzz" in queries[0] and "zzz" not in queries[2] and "zzz" not in queries[4]
    assert 'level!~"debug|trace"' in queries[4]
    prompt = answer_prompt(route)
    assert "severity=all" in prompt
    assert "no error-level lines matched" in prompt
    assert "does NOT mean the service is healthy" in prompt
    assert "Do NOT say that no errors" not in prompt


@respx.mock
async def test_ladder_keyword_drop_only(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(RANGE).mock(side_effect=[
        EMPTY, EMPTY,
        streams(("plex", "error", 1_793_826_000_000_000_000, "bad")), EMPTY,
    ])
    route = mock_ollama({**INTENT, "keywords": ["zzz"]})
    await client.post("/search", json={"query": "plex zzz"})
    prompt = answer_prompt(route)
    assert "keyword filter dropped" in prompt and "severity=errors" in prompt


@respx.mock
async def test_no_results(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(RANGE).mock(return_value=EMPTY)
    route = mock_ollama(INTENT)
    resp = await client.post("/search", json={"query": "did plex have issues?"})
    body = resp.json()
    assert body == {"answer": None, "answer_status": "no_results", "sources": []}
    assert answer_prompt(route) is None


@respx.mock
async def test_llm_unavailable_keeps_sources(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(RANGE).mock(side_effect=[
        streams(("plex", "error", 1_793_826_000_000_000_000, "x")), EMPTY])
    mock_ollama(INTENT, answer=None)
    body = (await client.post("/search", json={"query": "plex issues"})).json()
    assert body["answer_status"] == "llm_unavailable"
    assert body["answer"] is None
    assert len(body["sources"]) == 1


@respx.mock
async def test_intent_llm_down_still_searches(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(RANGE).mock(side_effect=[
        streams(("plex", "error", 1_793_826_000_000_000_000, "x")), EMPTY])
    mock_ollama(None)  # intent call fails, answer call succeeds
    body = (await client.post("/search", json={"query": "plex issues"})).json()
    assert body["answer_status"] == "ok"


@respx.mock
async def test_stats_route_uses_instant_query(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex", "sonarr"]}))
    range_route = respx.get(RANGE).mock(return_value=EMPTY)
    inst = respx.get(INSTANT).mock(side_effect=[
        httpx.Response(200, json={"data": {"result": [
            {"metric": {"container": "plex", "level": "error"}, "value": [1, "4"]},
            {"metric": {"container": "sonarr", "level": "info"}, "value": [1, "9"]},
        ]}}),
        httpx.Response(200, json={"data": {"result": []}}),
    ])
    route = mock_ollama({"hours": None, "severity": "all", "keywords": []}, "SHOULD NOT APPEAR")
    body = (await client.post("/search", json={"query": "which container has the most errors?"})).json()
    assert body["answer_status"] == "ok" and body["sources"] == []
    assert inst.call_count == 2
    assert not range_route.called
    assert "plex" in body["answer"] and "Containers with errors:** 1 of 2" in body["answer"]
    # only the intent call hit Ollama; stats are never phrased by the LLM
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["format"] == "json"


@respx.mock
async def test_stats_no_results(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(INSTANT).mock(return_value=httpx.Response(200, json={"status": "success"}))
    mock_ollama({})
    body = (await client.post("/search", json={"query": "how many containers have errors?"})).json()
    assert body["answer_status"] == "no_results"


@respx.mock
async def test_stats_always_deterministic(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(INSTANT).mock(side_effect=[
        httpx.Response(200, json={"data": {"result": [
            {"metric": {"container": "plex", "level": "error"}, "value": [1, "4"]}]}}),
        httpx.Response(200, json={"data": {"result": []}}),
    ])
    route = mock_ollama({}, answer=None)
    body = (await client.post("/search", json={"query": "how many lines?"})).json()
    assert route.call_count == 1
    assert body["answer_status"] == "ok"
    assert body["sources"] == []
    assert "SHOULD NOT" not in body["answer"]
    assert "`plex`" in body["answer"] and "Total log lines:** 4" in body["answer"]


@respx.mock
async def test_503_when_label_lookup_fails(client):
    respx.get(LABELS).mock(return_value=httpx.Response(500))
    resp = await client.post("/search", json={"query": "anything"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Log search unavailable"


@respx.mock
async def test_503_when_line_query_fails(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(RANGE).mock(return_value=httpx.Response(500))
    mock_ollama(INTENT)
    resp = await client.post("/search", json={"query": "plex issues"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Log search unavailable"


@respx.mock
async def test_503_when_count_query_fails(client):
    respx.get(LABELS).mock(return_value=httpx.Response(200, json={"data": ["plex"]}))
    respx.get(INSTANT).mock(return_value=httpx.Response(500))
    mock_ollama({})
    resp = await client.post("/search", json={"query": "how many lines?"})
    assert resp.status_code == 503


async def test_ingest_removed(client):
    resp = await client.post("/ingest")
    assert resp.status_code in (404, 405)


def test_is_meta_query_classification():
    from app.search.intent import _is_meta_query

    assert _is_meta_query("which container has the most errors")
    assert _is_meta_query("how many containers have errors")
    assert _is_meta_query("count the errors per service")
    assert _is_meta_query("list all containers")
    assert not _is_meta_query("did plex have buffering issues?")
    assert not _is_meta_query("any errors in sonarr?")
