from datetime import datetime, timezone

from app.search.stats import build_stats

Q1 = [
    ({"container": "plex", "level": "error"}, 3),
    ({"container": "plex", "level": "WARN"}, 2),
    ({"container": "plex", "level": "warning"}, 4),
    ({"container": "plex", "level": ""}, 10),
    ({"container": "sonarr", "level": "info"}, 100),
    ({"container": "radarr", "level": "fatal"}, 1),
    ({}, 7),
]
Q2 = [({"container": "plex"}, 5), ({"container": "authentik"}, 2)]


def test_build_stats_folding_and_sort():
    stats = build_stats(Q1, Q2)
    names = [s["container"] for s in stats]
    assert names == ["plex", "authentik", "radarr", "sonarr"]
    plex = stats[0]
    assert plex["total_lines"] == 19
    assert plex["error_lines_labeled"] == 3
    assert plex["error_lines_unlabeled"] == 5
    assert plex["warning_lines"] == 6
    assert plex["level_counts"] == {"error": 3, "warn": 2, "warning": 4, "unlabeled": 10}


def test_sort_ties_by_lines_then_name():
    q1 = [({"container": "b", "level": "info"}, 5), ({"container": "a", "level": "info"}, 5),
          ({"container": "c", "level": "info"}, 9)]
    assert [s["container"] for s in build_stats(q1, [])] == ["c", "a", "b"]


def test_render_stats_markdown():
    from app.search.stats import render_stats_markdown

    since = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    text = render_stats_markdown(build_stats(Q1, Q2), since)
    assert "2026-11-04 12:00 UTC" in text
    assert "**Total log lines:** 120" in text
    assert "**Containers with errors:** 3 of 4" in text
    assert text.index("`plex`") < text.index("`authentik`") < text.index("`radarr`")
    assert "`sonarr`" not in text
    assert "keyword matches (estimate)" in text


def test_render_stats_markdown_top10_and_no_errors():
    from app.search.stats import render_stats_markdown

    since = datetime(2026, 11, 4, tzinfo=timezone.utc)
    q1 = [({"container": f"c{i:02d}", "level": "error"}, i + 1) for i in range(15)]
    text = render_stats_markdown(build_stats(q1, []), since)
    assert text.count("\n- `") == 10
    text = render_stats_markdown(build_stats([({"container": "a", "level": "info"}, 3)], []), since)
    assert "No error lines" in text


def test_render_other_containers_and_label_separation():
    from app.search.stats import render_stats_markdown

    since = datetime(2026, 11, 4, tzinfo=timezone.utc)
    q1 = [({"container": f"c{i:02d}", "level": "error"}, i + 1) for i in range(12)]
    q1 += [({"container": "quiet-a", "level": "info"}, 1), ({"container": "quiet-b", "level": "info"}, 1)]
    text = render_stats_markdown(build_stats(q1, []), since)
    assert "Other containers (4): c01, c00, quiet-a, quiet-b" in text
    assert "labeled error lines" in text and "keyword matches (estimate)" in text
