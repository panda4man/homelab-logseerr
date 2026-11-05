from app.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.loki_url == "http://localhost:3100"
    assert s.ollama_url == "http://localhost:11434"
    assert s.ollama_model == "llama3"
    assert s.ollama_num_ctx == 8192
    assert s.loki_retention_days == 7
    assert s.search_default_hours == 24
    assert s.loki_query_limit == 1000
    assert s.search_max_lines == 200
    assert s.search_max_context_chars == 16000
    assert s.search_max_line_chars == 500


def test_removed_settings_are_gone():
    s = Settings()
    for name in ("embed_url", "embed_model", "qdrant_url", "ingest_interval_minutes",
                 "collection_name", "search_top_k", "search_min_score",
                 "aggregate_max_chunks", "loki_page_limit", "loki_max_pages"):
        assert not hasattr(s, name)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki:3100")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    s = Settings()
    assert s.loki_url == "http://loki:3100"
    assert s.ollama_model == "mistral"
