from app.search.loki import LogLine
from app.search.reduce import reduce_lines

S = 1_000_000_000


def L(c, lvl, t, text):
    return LogLine(c, lvl, t * S, text)


def test_dedupe_counts_newest_example():
    lines = [L("a", "info", i, f"req {i} took {i*10}ms id=0x{i:08x}") for i in range(1, 38)]
    srcs, st = reduce_lines(lines)
    assert len(srcs) == 1
    assert srcs[0]["log_text"].count("\n") == 0
    assert "(x37)" in srcs[0]["log_text"]
    assert "req 37 " in srcs[0]["log_text"]
    assert st.total_lines == 37 and st.shown_lines == 37 and not st.truncated


def test_uuid_and_timestamp_templated():
    lines = [
        L("a", "", 1, "2026-11-04T21:00:01Z job 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed"),
        L("a", "", 2, "2026-11-04T21:00:02Z job 9f2504e0-4f89-11d3-9a0c-0305e82c3302 failed"),
    ]
    srcs, _ = reduce_lines(lines)
    assert "(x2)" in srcs[0]["log_text"]


def test_line_truncation():
    srcs, _ = reduce_lines([L("a", "info", 1, "x" * 2000)], max_line_chars=500)
    assert max(len(l) for l in srcs[0]["log_text"].split("\n")) <= 520


def test_round_robin_fairness():
    lines = [L("noisy", "info", i, f"unique line {'abcdefghij'[i % 10]*i}") for i in range(1, 100)]
    lines += [L("quiet", "info", 500 + i, f"quiet {'z'*i}") for i in range(1, 4)]
    srcs, st = reduce_lines(lines, max_lines=10)
    by = {s["service"]: s for s in srcs}
    assert "quiet" in by
    assert by["quiet"]["log_text"].count("\n") + 1 == 3
    assert by["noisy"]["log_text"].count("\n") + 1 == 7
    assert st.truncated


def test_char_cap():
    lines = [L("a", "info", i, "w" * 100 + "q" * i) for i in range(1, 50)]
    srcs, st = reduce_lines(lines, max_lines=200, max_chars=1000)
    assert len(srcs[0]["log_text"]) <= 1000 + 60
    assert st.truncated and st.shown_lines < st.total_lines


def test_errors_first_and_container_ordering():
    lines = [
        L("calm", "info", 10, "c1"),
        L("bad", "info", 20, "ok msg"),
        L("bad", "error", 5, "oh no"),
    ]
    srcs, _ = reduce_lines(lines)
    assert [s["service"] for s in srcs] == ["bad", "calm"]
    assert srcs[0]["log_text"].splitlines()[0].endswith("oh no")  # oldest first
    assert srcs[0]["levels"] == ["error", "info"]
    # error entry is kept first under a tight cap
    srcs, _ = reduce_lines(lines, max_lines=1)
    assert "oh no" in srcs[0]["log_text"]


def test_no_score_and_shape():
    srcs, _ = reduce_lines([L("a", "info", 1, "hi")])
    assert set(srcs[0]) == {"service", "time_range", "log_text", "levels"}
    assert "score" not in srcs[0]


def test_time_range_format_and_multi_date():
    a = 1793826000  # 2026-11-04 21:00 UTC
    srcs, _ = reduce_lines([L("a", "", a, "one"), L("a", "", a + 300, "two")])
    assert srcs[0]["time_range"] == "2026-11-04 21:00–21:05 UTC"
    srcs, _ = reduce_lines([L("a", "", a, "one"), L("a", "", a + 86400, "two")])
    assert srcs[0]["time_range"] == "2026-11-04 21:00–2026-11-05 21:00 UTC"
