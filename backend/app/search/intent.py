"""Turn a natural-language question into a structured Loki query intent."""
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from .llm import generate_json

logger = logging.getLogger(__name__)

Severity = Literal["errors", "warnings", "all", "debug"]

_TIME_PATTERNS: list[tuple[re.Pattern, Callable]] = [
    (re.compile(r"(?:last|past)\s+(\d+)\s+hours?", re.I), lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r"(?:last|past)\s+(\d+)\s+days?", re.I), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r"(?:last|past)\s+(\d+)\s+weeks?", re.I), lambda m: timedelta(weeks=int(m.group(1)))),
    (re.compile(r"(?:last|past)\s+24\s+hours?", re.I), lambda _: timedelta(hours=24)),
    (re.compile(r"(?:last|past)\s+hour", re.I), lambda _: timedelta(hours=1)),
    (re.compile(r"(?:last|past)\s+week", re.I), lambda _: timedelta(weeks=1)),
    (re.compile(r"\btoday\b", re.I), lambda _: timedelta(hours=24)),
    (re.compile(r"\byesterday\b", re.I), lambda _: timedelta(hours=48)),
]


def _parse_time_filter(query: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(tz=timezone.utc)
    for pattern, delta_fn in _TIME_PATTERNS:
        m = pattern.search(query)
        if m:
            return now - delta_fn(m)
    return None


_META_PATTERNS: list[re.Pattern] = [
    re.compile(r"how many", re.I),
    re.compile(r"how much", re.I),
    re.compile(r"number of", re.I),
    re.compile(r"\bcount\b", re.I),
    re.compile(r"which (container|service|app)", re.I),
    re.compile(r"what (container|service|app)", re.I),
    re.compile(r"most (errors?|warnings?)", re.I),
    re.compile(r"\bdistinct\b", re.I),
    re.compile(r"across all", re.I),
    re.compile(r"list (the |all )?(containers|services|apps)", re.I),
]


def _is_meta_query(query: str) -> bool:
    return any(pattern.search(query) for pattern in _META_PATTERNS)


@dataclass
class QueryIntent:
    mode: Literal["logs", "stats"]
    containers: list[str]
    since: datetime
    severity: Severity
    keywords: list[str]


_STOP_WORDS = {
    "the", "and", "for", "any", "are", "was", "were", "did", "does", "has", "have",
    "had", "with", "from", "that", "this", "what", "which", "when", "where", "why",
    "how", "last", "past", "hours", "hour", "days", "day", "week", "weeks", "today",
    "yesterday", "about", "show", "tell", "all", "logs", "log", "service", "services",
    "container", "containers", "app", "apps", "not", "now", "there", "their", "into",
    "than", "then", "been", "being", "can", "could", "would", "should", "most",
    "many", "much", "list", "count", "number", "across", "distinct",
    "error", "errors", "logged", "issue", "issues", "problem", "problems", "wrong",
    "anything", "show", "mentioning",
}
_GENERIC_KEYWORDS = {
    "issue", "issues", "error", "errors", "problem", "problems", "fail", "failure",
    "failures", "failed", "crash", "crashes", "wrong", "warning", "warnings", "warn",
    "log", "logs", "bug", "bugs", "trouble", "down", "debug", "trace", "info",
    "service", "services", "container", "containers", "app", "apps",
}
_SEVERITIES = {"errors", "warnings", "all", "debug"}
_ERR_RE = re.compile(r"error|issue|problem|fail|crash|wrong|down", re.I)
_WARN_RE = re.compile(r"warn", re.I)
_DEBUG_RE = re.compile(r"debug|trace", re.I)


def _heuristic_severity(query: str) -> Severity:
    if _ERR_RE.search(query):
        return "errors"
    if _WARN_RE.search(query):
        return "warnings"
    if _DEBUG_RE.search(query):
        return "debug"
    return "all"


def _match_containers(query: str, known: list[str]) -> tuple[list[str], set[str]]:
    """Return (containers, query words that matched). Exact full names win."""
    ql = query.lower()
    spans: list[tuple[int, int, str]] = []
    for c in sorted(known, key=len, reverse=True):
        for m in re.finditer(rf"(?<![a-z0-9_-]){re.escape(c.lower())}(?![a-z0-9_-])", ql):
            if not any(s0 <= m.start() and m.end() <= e0 for s0, e0, _ in spans):
                spans.append((m.start(), m.end(), c))
    if spans:
        names = [c for _, _, c in sorted(spans)]
        out = list(dict.fromkeys(names))
        return out, {c.lower() for c in out}

    words = [
        w for w in re.findall(r"[a-z0-9]+", ql)
        if len(w) >= 3 and w not in _STOP_WORDS
    ]
    out: list[str] = []
    matched: set[str] = set()
    for c in known:
        cl = c.lower()
        for w in words:
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", cl):
                if c not in out:
                    out.append(c)
                matched.add(w)
    return out, matched


def _fallback_containers(query: str, known: list[str]) -> list[str]:
    return _match_containers(query, known)[0]


def _clamp_since(
    since: datetime, now: datetime, retention_days: int
) -> datetime:
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since = since.astimezone(timezone.utc)
    return max(since, now - timedelta(days=retention_days))


def _build_prompt(query: str) -> str:
    return (
        "You convert a question about homelab logs into a JSON search plan.\n"
        "Respond with JSON only: "
        '{"hours": int|null, "severity": "errors|warnings|all|debug", "keywords": [...]}\n'
        "Rules:\n"
        "- hours: the look-back window in hours if the question states one, else null.\n"
        "- severity: errors for problems/issues/failures, warnings for warnings, debug if "
        "debug/trace logs are requested, otherwise all.\n"
        "- keywords: ONLY specific literal terms the user named (an error code, a username, a "
        "filename). NEVER generic words like issue, error, problem, warning, fail. "
        "Usually empty.\n\n"
        f"Question: {query}\nJSON:"
    )


async def parse_intent(
    query: str,
    known_containers: list[str],
    ollama_url: str,
    model: str,
    *,
    num_ctx: int = 8192,
    default_hours: int = 24,
    retention_days: int = 7,
    now: datetime | None = None,
) -> QueryIntent:
    now = now or datetime.now(tz=timezone.utc)
    mode = "stats" if _is_meta_query(query) else "logs"

    data: dict = {}
    raw = await generate_json(
        ollama_url, model, _build_prompt(query), num_ctx=num_ctx, timeout=60
    )
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, TypeError):
            logger.warning("Intent LLM returned invalid JSON")

    # containers: deterministic only; LLM-suggested containers are ignored
    containers, matched_words = _match_containers(query, known_containers)

    # severity: a non-"all" heuristic wins; the LLM only refines "all"
    severity: Severity = _heuristic_severity(query)
    sev = data.get("severity")
    if severity == "all" and isinstance(sev, str) and sev.lower() in _SEVERITIES:
        severity = sev.lower()  # type: ignore[assignment]

    # keywords (LLM only; fallback is empty)
    keywords: list[str] = []
    raw_kw = data.get("keywords")
    if isinstance(raw_kw, list):
        for k in raw_kw:
            if not isinstance(k, str):
                continue
            k = k.replace("`", "").strip()
            if k and k.lower() not in _GENERIC_KEYWORDS and k not in keywords:
                keywords.append(k)
    # drop keywords that merely restate the selected containers
    redundant = set(matched_words)
    for c in containers:
        redundant.add(c.lower())
        redundant.update(t for t in re.split(r"[-_]", c.lower()) if t)
    keywords = [k for k in keywords if k.lower() not in redundant][:5]

    # window: regex wins, then LLM hours, then default
    since = _parse_time_filter(query, now)
    if since is None:
        hours = data.get("hours")
        if isinstance(hours, int) and not isinstance(hours, bool) and hours > 0:
            since = now - timedelta(hours=hours)
        else:
            since = now - timedelta(hours=default_hours)
    since = _clamp_since(since, now, retention_days)

    return QueryIntent(mode, containers, since, severity, keywords)
