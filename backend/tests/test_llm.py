import json
import respx
import httpx
from app.search.llm import generate_answer, generate_json

OLLAMA_URL = "http://ollama:11434"
MODEL = "llama3"
SOURCES = [
    {
        "service": "plex",
        "time_range": "2024-05-27 21:00–21:05 UTC",
        "log_text": "buffering error: stream stalled at 720p",
    }
]


@respx.mock
async def test_generate_answer_returns_string():
    respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "Plex had a buffering error."})
    )
    result = await generate_answer(OLLAMA_URL, MODEL, "did plex have issues?", SOURCES)
    assert result == "Plex had a buffering error."


@respx.mock
async def test_generate_answer_includes_sources_and_query_in_prompt():
    route = respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "answer"})
    )
    await generate_answer(OLLAMA_URL, MODEL, "any plex issues?", SOURCES)
    body = json.loads(route.calls[0].request.content)
    assert "buffering error" in body["prompt"]
    assert "any plex issues?" in body["prompt"]
    assert body["stream"] is False
    assert body["options"] == {"num_ctx": 8192}
    assert "format" not in body
    assert body["think"] is False


@respx.mock
async def test_generate_answer_includes_scope_and_absence_caveat():
    route = respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "answer"})
    )
    await generate_answer(OLLAMA_URL, MODEL, "q", SOURCES, scope="containers=[plex], severity=all", num_ctx=4096)
    body = json.loads(route.calls[0].request.content)
    assert "containers=[plex], severity=all" in body["prompt"]
    assert "does NOT mean the service is healthy" in body["prompt"]
    assert body["options"]["num_ctx"] == 4096


@respx.mock
async def test_generate_json_sends_format_and_timeout():
    route = respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "{}"})
    )
    assert await generate_json(OLLAMA_URL, MODEL, "p") == "{}"
    body = json.loads(route.calls[0].request.content)
    assert body["format"] == "json"
    assert body["think"] is False


@respx.mock
async def test_generate_answer_returns_none_on_http_error():
    respx.post(f"{OLLAMA_URL}/api/generate").mock(return_value=httpx.Response(503))
    result = await generate_answer(OLLAMA_URL, MODEL, "question", SOURCES)
    assert result is None


async def test_generate_answer_returns_none_on_connect_error():
    result = await generate_answer(
        "http://unreachable-host-xyz:11434", MODEL, "question", SOURCES
    )
    assert result is None


@respx.mock
async def test_prompt_matched_variant_for_errors():
    route = respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "x"}))
    await generate_answer(OLLAMA_URL, MODEL, "q", SOURCES, scope="s", severity="errors")
    prompt = json.loads(route.calls[0].request.content)["prompt"]
    assert "matched the errors filter" in prompt
    assert "Do NOT say that no errors" in prompt
    assert "does NOT mean the service is healthy" not in prompt


@respx.mock
async def test_prompt_fallback_variant():
    route = respx.post(f"{OLLAMA_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "x"}))
    await generate_answer(OLLAMA_URL, MODEL, "q", SOURCES, scope="s", severity="all", fell_back=True)
    prompt = json.loads(route.calls[0].request.content)["prompt"]
    assert "No lines matched" in prompt
    assert "does NOT mean the service is healthy" in prompt
    assert "Do NOT say that no errors" not in prompt
