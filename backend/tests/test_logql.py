from app.search.logql import (
    ERROR_KEYWORD_RE,
    count_queries,
    line_queries,
    selector,
)


def test_selector_variants():
    assert selector([]) == '{container!=""}'
    assert selector(["plex"]) == "{container=`plex`}"
    assert selector(["a", "b"]) == "{container=~`a|b`}"
    assert selector([], 'level=""') == '{container!="", level=""}'


def test_errors_two_queries():
    qs = line_queries(["plex"], "errors", [])
    assert len(qs) == 2
    assert qs[0].startswith('{container=`plex`, level=~"error|err|critical')
    assert qs[1].startswith('{container=`plex`, level=""} |~ ')
    assert ERROR_KEYWORD_RE in qs[1]


def test_warnings_includes_warn_levels_and_keywords():
    qs = line_queries([], "warnings", [])
    assert len(qs) == 2
    assert "warn|warning" in qs[0] and "error" in qs[0]
    assert "warn|warning" in qs[1]


def test_all_uses_negative_level_match_keeping_unlabeled():
    qs = line_queries(["x"], "all", [])
    assert qs == ['{container=`x`, level!~"debug|trace"}']


def test_debug_is_bare_selector():
    assert line_queries(["x"], "debug", []) == ["{container=`x`}"]


def test_keywords_added_and_escaped():
    qs = line_queries(["x"], "all", ["a.b", "c(d)"])
    assert qs[0].endswith(r" |~ `(?i)a\.b|c\(d\)`")


def test_container_regex_escaped():
    assert selector(["a.b", "c+d"]) == r"{container=~`a\.b|c\+d`}"


def test_backticks_stripped():
    qs = line_queries(["x"], "all", ["ev`il"])
    assert "ev`il" not in qs[0]
    assert "evil" in qs[0]
    assert selector(["a`b"]) == "{container=`ab`}"
    assert line_queries(["x"], "all", ["`"]) == ['{container=`x`, level!~"debug|trace"}']


def test_count_queries():
    q1, q2 = count_queries(["plex"], ["kw"], 3600)
    assert q1 == "sum by (container, level) (count_over_time({container=`plex`} |~ `(?i)kw` [3600s]))"
    assert q2.startswith("sum by (container) (count_over_time({container=`plex`, level=\"\"} |~ ")
    assert "`(?i)kw`" in q2 and q2.endswith("[3600s]))")
