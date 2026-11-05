import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import settings
from .search.intent import (  # noqa: F401  (re-exported for callers/tests)
    QueryIntent,
    _is_meta_query,
    _parse_time_filter,
    parse_intent,
)
from .search.llm import generate_answer
from .search.logql import count_queries, line_queries
from .search.loki import LogLine, list_containers, query_counts, query_lines
from .search.reduce import reduce_lines
from .search.stats import build_stats, render_stats_markdown

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="logseerr")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    answer: str | None
    answer_status: str
    sources: list[dict]


def _loki_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Log search unavailable")


@app.get("/health")
async def health():
    checks: dict[str, str] = {}
    for name, url in (
        ("loki", f"{settings.loki_url}/loki/api/v1/labels"),
        ("llm", f"{settings.ollama_url}/api/tags"),
    ):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=5)
                resp.raise_for_status()
            checks[name] = "ok"
        except Exception:
            checks[name] = "unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=200 if all_ok else 503,
    )


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _describe_scope(
    containers: list[str], since: datetime, severity: str, note: str = ""
) -> str:
    names = ", ".join(containers) if containers else "all"
    sev = severity + (f" ({note})" if note else "")
    return f"containers=[{names}], window=since {since:%Y-%m-%d %H:%M} UTC, severity={sev}"


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    now = datetime.now(tz=timezone.utc)
    retention_start = now - timedelta(days=settings.loki_retention_days)

    try:
        known = await list_containers(settings.loki_url, _ns(retention_start), _ns(now))
    except Exception:
        logger.exception("Loki label lookup failed")
        raise _loki_unavailable()

    intent = await parse_intent(
        req.query,
        known,
        settings.ollama_url,
        settings.ollama_model,
        num_ctx=settings.ollama_num_ctx,
        default_hours=settings.search_default_hours,
        retention_days=settings.loki_retention_days,
        now=now,
    )

    if intent.mode == "stats":
        return await _search_stats(req.query, intent, now)
    return await _search_logs(req.query, intent, now)


async def _search_stats(query: str, intent: QueryIntent, now: datetime) -> SearchResponse:
    range_seconds = max(int((now - intent.since).total_seconds()), 1)
    q1, q2 = count_queries(intent.containers, intent.keywords, range_seconds)
    try:
        q1_rows = await query_counts(settings.loki_url, q1, _ns(now))
        q2_rows = await query_counts(settings.loki_url, q2, _ns(now))
    except Exception:
        logger.exception("Loki count query failed")
        raise _loki_unavailable()

    if not q1_rows:
        return SearchResponse(answer=None, answer_status="no_results", sources=[])

    per_container = build_stats(q1_rows, q2_rows)
    answer = render_stats_markdown(per_container, intent.since, intent.keywords)
    return SearchResponse(answer=answer, answer_status="ok", sources=[])


async def _fetch_lines(
    intent: QueryIntent, severity: str, keywords: list[str], now: datetime
) -> list[LogLine]:
    merged: list[LogLine] = []
    for logql in line_queries(intent.containers, severity, keywords):
        merged.extend(
            await query_lines(
                settings.loki_url, logql, _ns(intent.since), _ns(now), settings.loki_query_limit
            )
        )
    return merged


async def _search_logs(query: str, intent: QueryIntent, now: datetime) -> SearchResponse:
    attempts: list[tuple[str, list[str], str]] = [(intent.severity, intent.keywords, "")]
    if intent.keywords:
        attempts.append((intent.severity, [], "keyword filter dropped; no lines matched it"))
    if intent.severity in ("errors", "warnings"):
        attempts.append(
            (
                "all",
                [],
                f"no {'error-level' if intent.severity == 'errors' else 'warning or error'} "
                "lines matched; showing other recent lines",
            )
        )

    lines: list[LogLine] = []
    used: tuple[str, list[str], str] = attempts[0]
    try:
        for attempt in attempts:
            lines = await _fetch_lines(intent, attempt[0], attempt[1], now)
            used = attempt
            if lines:
                break
    except Exception:
        logger.exception("Loki line query failed")
        raise _loki_unavailable()

    if not lines:
        return SearchResponse(answer=None, answer_status="no_results", sources=[])

    sources, stats = reduce_lines(
        lines,
        max_lines=settings.search_max_lines,
        max_chars=settings.search_max_context_chars,
        max_line_chars=settings.search_max_line_chars,
    )
    severity, keywords, note = used
    scope = _describe_scope(intent.containers, intent.since, severity, note)
    if keywords:
        scope += f", keywords=[{', '.join(keywords)}]"
    if stats.truncated:
        scope += f". Showing {stats.shown_lines} of {stats.total_lines} lines"

    answer = await generate_answer(
        settings.ollama_url, settings.ollama_model, query, sources, scope,
        num_ctx=settings.ollama_num_ctx,
        severity=severity,
        fell_back=bool(note),
    )
    if answer is None:
        return SearchResponse(answer=None, answer_status="llm_unavailable", sources=sources)
    return SearchResponse(answer=answer, answer_status="ok", sources=sources)
