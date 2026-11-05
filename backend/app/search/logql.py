"""Pure LogQL builders. All user-derived values are escaped and backtick-quoted."""
import re

ERROR_LEVELS = ["error", "err", "critical", "crit", "fatal", "emergency", "emerg", "alert", "panic"]
WARN_LEVELS = ["warn", "warning"]
ERROR_KEYWORD_RE = r"(?i)\b(error|fatal|panic|exception|critical)\b"
WARN_KEYWORD_RE = r"(?i)\b(warn|warning)\b"


def _bt(value: str) -> str:
    return "`" + value.replace("`", "") + "`"


def selector(containers: list[str], extra: str = "") -> str:
    extra_part = f", {extra}" if extra else ""
    cs = [c for c in containers if c]
    if not cs:
        return '{container!=""' + extra_part + "}"
    if len(cs) == 1:
        return "{" + f'container={_bt(cs[0])}' + extra_part + "}"
    alt = "|".join(re.escape(c) for c in cs)
    return "{" + f"container=~{_bt(alt)}" + extra_part + "}"


def _keyword_filter(keywords: list[str]) -> str:
    kws = [k.replace("`", "").strip() for k in keywords]
    kws = [k for k in kws if k]
    if not kws:
        return ""
    return " |~ " + _bt("(?i)" + "|".join(re.escape(k) for k in kws))


def _levels(levels: list[str]) -> str:
    return "|".join(levels)


def line_queries(
    containers: list[str], severity: str, keywords: list[str]
) -> list[str]:
    kw = _keyword_filter(keywords)
    if severity in ("errors", "warnings"):
        levels = ERROR_LEVELS if severity == "errors" else ERROR_LEVELS + WARN_LEVELS
        kw_re = ERROR_KEYWORD_RE if severity == "errors" else (
            r"(?i)\b(error|fatal|panic|exception|critical|warn|warning)\b"
        )
        labeled = selector(containers, f'level=~"{_levels(levels)}"') + kw
        unlabeled = selector(containers, 'level=""') + f" |~ {_bt(kw_re)}" + kw
        return [labeled, unlabeled]
    if severity == "debug":
        return [selector(containers) + kw]
    return [selector(containers, 'level!~"debug|trace"') + kw]


def count_queries(
    containers: list[str], keywords: list[str], range_seconds: int
) -> tuple[str, str]:
    kw = _keyword_filter(keywords)
    n = max(int(range_seconds), 1)
    q1 = f"sum by (container, level) (count_over_time({selector(containers)}{kw} [{n}s]))"
    unlabeled = selector(containers, 'level=""')
    q2 = (
        f"sum by (container) (count_over_time("
        f"{unlabeled} |~ {_bt(ERROR_KEYWORD_RE)}{kw} [{n}s]))"
    )
    return q1, q2
