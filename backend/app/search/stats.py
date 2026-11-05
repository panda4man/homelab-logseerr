"""Deterministic per-container statistics built from Loki metric queries."""
from datetime import datetime

from .logql import ERROR_LEVELS, WARN_LEVELS


def build_stats(q1_rows: list[tuple[dict, int]], q2_rows: list[tuple[dict, int]]) -> list[dict]:
    per: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return per.setdefault(
            name,
            {
                "container": name,
                "total_lines": 0,
                "error_lines_labeled": 0,
                "error_lines_unlabeled": 0,
                "warning_lines": 0,
                "level_counts": {},
            },
        )

    for metric, n in q1_rows:
        name = metric.get("container") or ""
        if not name:
            continue
        level = (metric.get("level") or "").lower()
        e = entry(name)
        e["total_lines"] += n
        key = level or "unlabeled"
        e["level_counts"][key] = e["level_counts"].get(key, 0) + n
        if level in ERROR_LEVELS:
            e["error_lines_labeled"] += n
        elif level in WARN_LEVELS:
            e["warning_lines"] += n
    for metric, n in q2_rows:
        name = metric.get("container") or ""
        if name:
            entry(name)["error_lines_unlabeled"] += n

    return sorted(
        per.values(),
        key=lambda e: (
            -(e["error_lines_labeled"] + e["error_lines_unlabeled"]),
            -e["total_lines"],
            e["container"],
        ),
    )


def render_stats_markdown(
    per_container: list[dict], since: datetime, keywords: list[str] | None = None, top_n: int = 10
) -> str:
    total = sum(c["total_lines"] for c in per_container)
    with_errors = [
        c for c in per_container if c["error_lines_labeled"] + c["error_lines_unlabeled"] > 0
    ]
    out = [
        f"**Window:** since {since:%Y-%m-%d %H:%M} UTC",
        f"**Total log lines:** {total}",
        f"**Containers with errors:** {len(with_errors)} of {len(per_container)}",
    ]
    if keywords:
        out.append(f"**Keyword filter:** {', '.join(keywords)}")
    if with_errors:
        out += [
            "",
            f"**Top {min(top_n, len(with_errors))} containers by errors** "
            "(ranked by labeled + unlabeled):",
            "",
        ]
        for c in with_errors[:top_n]:
            out.append(
                f"- `{c['container']}`: **{c['error_lines_labeled']} labeled error lines** "
                f"(level label is error-class) + "
                f"~{c['error_lines_unlabeled']} unlabeled-line keyword matches (estimate); "
                f"{c['total_lines']} lines total"
            )
    else:
        out += ["", "No error lines found in this window."]
    shown = {c["container"] for c in with_errors[:top_n]}
    others = [c["container"] for c in per_container if c["container"] not in shown]
    if others:
        out += ["", f"Other containers ({len(others)}): {', '.join(others)}"]
    out += ["", "_Counts are individual log lines. Unlabeled-error figures are keyword matches and are estimates._"]
    return "\n".join(out)
