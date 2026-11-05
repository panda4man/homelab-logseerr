import json
from datetime import datetime, timedelta, timezone

import httpx
import respx

from app.search.intent import _is_meta_query, _parse_time_filter, parse_intent

OLLAMA = "http://ollama:11434"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
AUTH = ["homelab-authentik-server-1", "homelab-authentik-worker-1", "homelab-authentik-redis-1"]
KNOWN = AUTH + ["plex", "sonarr", "crafty_container", "loki"]


def llm(payload):
    respx.post(f"{OLLAMA}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": json.dumps(payload)})
    )


async def run(q, **kw):
    return await parse_intent(q, KNOWN, OLLAMA, "m", now=NOW, **kw)


def test_past_and_last_accepted():
    assert _parse_time_filter("in the past 3 hours") is not None
    assert _parse_time_filter("in the last 3 hours") is not None


def test_is_meta_query():
    assert _is_meta_query("which container has the most errors")
    assert not _is_meta_query("any errors in sonarr?")


@respx.mock
async def test_regex_beats_llm():
    llm({"containers": ["plex"], "hours": 100, "severity": "errors", "keywords": []})
    i = await run("plex errors in the last 2 hours")
    assert i.since == NOW - timedelta(hours=2)
    assert i.containers == ["plex"]
    assert i.mode == "logs"
    assert i.since.tzinfo is not None


@respx.mock
async def test_llm_hours_then_default():
    llm({"containers": [], "hours": 5, "severity": "all", "keywords": []})
    assert (await run("what happened")).since == NOW - timedelta(hours=5)
    llm({"containers": [], "hours": None, "severity": "all", "keywords": []})
    assert (await run("what happened")).since == NOW - timedelta(hours=24)
    llm({"containers": [], "hours": -3, "severity": "all", "keywords": []})
    assert (await run("what happened")).since == NOW - timedelta(hours=24)


@respx.mock
async def test_clamp_to_retention():
    llm({"containers": [], "hours": None, "severity": "all", "keywords": []})
    i = await run("errors in the last 30 days")
    assert i.since == NOW - timedelta(days=7)
    llm({"containers": [], "hours": 5000, "severity": "all", "keywords": []})
    assert (await run("what happened")).since == NOW - timedelta(days=7)


@respx.mock
async def test_invalid_severity_uses_heuristic():
    llm({"containers": [], "hours": None, "severity": "bogus", "keywords": []})
    assert (await run("any problems?")).severity == "errors"
    assert (await run("show warnings")).severity == "warnings"
    assert (await run("show debug output")).severity == "debug"
    assert (await run("what happened")).severity == "all"


@respx.mock
async def test_bad_json_falls_back():
    respx.post(f"{OLLAMA}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "not json"})
    )
    i = await run("did plex have any issues?")
    assert i.containers == ["plex"]
    assert i.severity == "errors"
    assert i.keywords == []


@respx.mock
async def test_llm_down_falls_back():
    respx.post(f"{OLLAMA}/api/generate").mock(return_value=httpx.Response(503))
    i = await run("sonarr warnings")
    assert i.containers == ["sonarr"]
    assert i.severity == "warnings"


@respx.mock
async def test_generic_keywords_removed_and_capped():
    llm({"containers": [], "hours": None, "severity": "errors",
         "keywords": ["issue", "Error", "k1", "k2", "k3", "k4", "k5", "k6", "`bt`"]})
    i = await run("x")
    assert i.keywords == ["k1", "k2", "k3", "k4", "k5"]


@respx.mock
async def test_request_uses_json_format_and_known_list():
    route = respx.post(f"{OLLAMA}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "{}"})
    )
    await run("hi there")
    body = json.loads(route.calls[0].request.content)
    assert body["format"] == "json"
    assert "plex" not in body["prompt"]
    assert "containers" not in body["prompt"].split("Question:")[0].lower().replace("containers/", "")
    assert body["options"]["num_ctx"] == 8192


@respx.mock
async def test_stats_mode_from_meta():
    llm({})
    assert (await run("how many containers have errors")).mode == "stats"


@respx.mock
async def test_llm_containers_ignored():
    llm({"containers": ["crafty_container", "loki"], "hours": None, "severity": "all", "keywords": []})
    assert (await run("what happened")).containers == []


@respx.mock
async def test_meta_queries_have_no_containers():
    llm({"containers": ["crafty_container"], "severity": "errors"})
    assert (await run("which container has the most errors")).containers == []
    assert (await run("how many containers logged errors today")).containers == []


@respx.mock
async def test_authentik_matches_all_three():
    llm({"containers": ["plex"], "severity": "errors"})
    i = await run("anything wrong with authentik today")
    assert sorted(i.containers) == sorted(AUTH)


@respx.mock
async def test_fallback_when_llm_down():
    respx.post(f"{OLLAMA}/api/generate").mock(return_value=httpx.Response(503))
    assert (await run("how is plex doing")).containers == ["plex"]


GITEA = ["gitea-runner", "homelab-gitea", "homelab-gitea-db"]


async def run_k(q, known, payload):
    llm(payload)
    return await parse_intent(q, known, OLLAMA, "m", now=NOW)


@respx.mock
async def test_keyword_equal_to_container_dropped_literal_kept():
    i = await run_k("show sonarr errors in the last 6 hours", KNOWN,
                    {"severity": "errors", "keywords": ["sonarr"]})
    assert i.containers == ["sonarr"] and i.keywords == []
    i = await run_k("show sonarr E404", KNOWN, {"severity": "errors", "keywords": ["sonarr", "E404"]})
    assert i.keywords == ["E404"]


@respx.mock
async def test_keyword_matching_container_token_dropped():
    i = await run_k("authentik problems", KNOWN, {"keywords": ["server", "Authentik", "E404"]})
    assert i.keywords == ["E404"]


@respx.mock
async def test_exact_container_name_wins():
    i = await run_k("gitea-runner", GITEA, {"keywords": ["gitea-runner"]})
    assert i.containers == ["gitea-runner"] and i.keywords == []
    i = await run_k("check homelab-gitea-db logs", GITEA, {})
    assert i.containers == ["homelab-gitea-db"]


@respx.mock
async def test_partial_name_still_matches_all():
    assert sorted((await run_k("gitea", GITEA, {})).containers) == sorted(GITEA)
    assert sorted((await run_k("authentik", KNOWN, {})).containers) == sorted(AUTH)


@respx.mock
async def test_heuristic_severity_beats_llm():
    i = await run_k("anything wrong with authentik", KNOWN, {"severity": "all"})
    assert i.severity == "errors"
    i = await run_k("what is plex up to", KNOWN, {"severity": "warnings"})
    assert i.severity == "warnings"
