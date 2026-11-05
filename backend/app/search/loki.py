"""Thin async Loki HTTP client. HTTP errors propagate to the caller."""
from dataclasses import dataclass

import httpx

TIMEOUT = 120


@dataclass
class LogLine:
    container: str
    level: str
    ts_ns: int
    line: str


async def list_containers(loki_url: str, start_ns: int, end_ns: int) -> list[str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{loki_url}/loki/api/v1/label/container/values",
            params={"start": start_ns, "end": end_ns},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    return sorted(v for v in (resp.json().get("data") or []) if v)


async def query_lines(
    loki_url: str, logql: str, start_ns: int, end_ns: int, limit: int
) -> list[LogLine]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{loki_url}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": start_ns,
                "end": end_ns,
                "limit": limit,
                "direction": "backward",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    result = (resp.json().get("data") or {}).get("result") or []
    lines: list[LogLine] = []
    for stream in result:
        labels = stream.get("stream") or {}
        container = labels.get("container", "")
        level = (labels.get("level") or "").lower()
        for ts, text in stream.get("values") or []:
            lines.append(LogLine(container, level, int(ts), text))
    return lines


async def query_counts(
    loki_url: str, logql: str, time_ns: int
) -> list[tuple[dict, int]]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{loki_url}/loki/api/v1/query",
            params={"query": logql, "time": time_ns},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    result = (resp.json().get("data") or {}).get("result") or []
    return [
        (item.get("metric") or {}, int(float(item["value"][1]))) for item in result
    ]
