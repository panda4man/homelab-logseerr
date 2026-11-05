"""Application configuration via pydantic-settings.

All settings can be overridden by environment variables or a .env file
(loaded relative to the current working directory).

Testing note: always instantiate ``Settings()`` directly in tests rather than
importing the module-level ``settings`` singleton. The singleton is created at
import time and will not reflect ``monkeypatch.setenv`` changes.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    loki_url: str = "http://localhost:3100"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_num_ctx: int = 8192
    loki_retention_days: int = 7
    search_default_hours: int = 24
    loki_query_limit: int = 1000
    search_max_lines: int = 200
    search_max_context_chars: int = 16000
    search_max_line_chars: int = 500

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
