"""Reduce raw Loki lines to a small, prompt-safe set of per-container sources."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .logql import ERROR_KEYWORD_RE, ERROR_LEVELS
from .loki import LogLine

_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX = re.compile(r"\b(?:0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})\b")
_DIGITS = re.compile(r"\d+")
_ERR_KW = re.compile(ERROR_KEYWORD_RE)


@dataclass
class ReduceStats:
    total_lines: int
    shown_lines: int
    truncated: bool


def _template(text: str) -> str:
    t = _ISO_TS.sub("<TS>", text)
    t = _UUID.sub("<UUID>", t)
    t = _HEX.sub("<HEX>", t)
    return _DIGITS.sub("<N>", t)


def _is_error(level: str, text: str) -> bool:
    if level:
        return level in ERROR_LEVELS
    return bool(_ERR_KW.search(text))


@dataclass
class _Entry:
    ts_ns: int
    level: str
    text: str
    count: int
    is_error: bool

    def render(self) -> str:
        suffix = f" (x{self.count})" if self.count > 1 else ""
        lvl = f"[{self.level}] " if self.level else ""
        return f"{lvl}{self.text}{suffix}"


def _fmt(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)


def _time_range(first_ns: int, last_ns: int) -> str:
    a, b = _fmt(first_ns), _fmt(last_ns)
    if a.date() == b.date():
        return f"{a:%Y-%m-%d %H:%M}–{b:%H:%M} UTC"
    return f"{a:%Y-%m-%d %H:%M}–{b:%Y-%m-%d %H:%M} UTC"


def reduce_lines(
    lines: list[LogLine],
    max_lines: int = 200,
    max_chars: int = 16000,
    max_line_chars: int = 500,
) -> tuple[list[dict], ReduceStats]:
    total = len(lines)
    # container -> template -> entry
    groups: dict[str, dict[str, _Entry]] = {}
    for ln in lines:
        text = ln.line.strip()
        if len(text) > max_line_chars:
            text = text[: max_line_chars - 1] + "…"
        by_tpl = groups.setdefault(ln.container, {})
        key = _template(text)
        entry = by_tpl.get(key)
        if entry is None:
            by_tpl[key] = _Entry(ln.ts_ns, ln.level, text, 1, _is_error(ln.level, ln.line))
        else:
            entry.count += 1
            if ln.ts_ns > entry.ts_ns:
                entry.ts_ns, entry.level, entry.text = ln.ts_ns, ln.level, text
                entry.is_error = _is_error(ln.level, ln.line)

    ordered: list[tuple[str, list[_Entry]]] = []
    for container, by_tpl in groups.items():
        entries = sorted(by_tpl.values(), key=lambda e: (not e.is_error, -e.ts_ns))
        ordered.append((container, entries))
    ordered.sort(
        key=lambda ce: (
            -sum(e.count for e in ce[1] if e.is_error),
            -sum(e.count for e in ce[1]),
            ce[0],
        )
    )

    chosen: dict[str, list[_Entry]] = {c: [] for c, _ in ordered}
    cursors = {c: 0 for c, _ in ordered}
    done: set[str] = set()
    used_lines = 0
    used_chars = 0
    while len(done) < len(ordered) and used_lines < max_lines:
        for container, entries in ordered:
            if container in done:
                continue
            if used_lines >= max_lines:
                break
            i = cursors[container]
            if i >= len(entries):
                done.add(container)
                continue
            rendered = entries[i].render()
            cost = len(rendered) + 1
            if used_chars + cost > max_chars:
                done.add(container)
                continue
            chosen[container].append(entries[i])
            cursors[container] = i + 1
            used_lines += 1
            used_chars += cost

    sources: list[dict] = []
    shown = 0
    for container, _ in ordered:
        picked = chosen[container]
        if not picked:
            continue
        shown += sum(e.count for e in picked)
        picked_sorted = sorted(picked, key=lambda e: e.ts_ns)
        sources.append(
            {
                "service": container,
                "time_range": _time_range(picked_sorted[0].ts_ns, picked_sorted[-1].ts_ns),
                "log_text": "\n".join(e.render() for e in picked_sorted),
                "levels": sorted({e.level for e in picked if e.level}),
            }
        )
    return sources, ReduceStats(total, shown, shown < total)
