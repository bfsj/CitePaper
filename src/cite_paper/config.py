"""Explicit, process-level configuration; never accept provider URLs from API callers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path = Path(".citepaper/citepaper.sqlite3")
    api_keys: dict[str, str] = field(default_factory=dict)  # secret -> tenant
    embedded_worker: bool = False
    lease_seconds: float = 60
    max_attempts: int = 3
    max_pending: int = 30
    max_pdf_bytes: int = 20 * 1024 * 1024
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout: float = 45
    allow_arxiv: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        keys = json.loads(os.getenv("CITEPAPER_API_KEYS", "{}"))
        if not isinstance(keys, dict) or any(
            not isinstance(k, str) or len(k) < 16 or not isinstance(v, str) or not v
            for k, v in keys.items()
        ):
            raise ValueError("CITEPAPER_API_KEYS must map keys (at least 16 characters) to tenants")
        base = os.getenv("CITEPAPER_LLM_BASE_URL", "").rstrip("/")
        model = os.getenv("CITEPAPER_LLM_MODEL", "")
        if bool(base) != bool(model):
            raise ValueError("Set both CITEPAPER_LLM_BASE_URL and CITEPAPER_LLM_MODEL")
        timeout = float(os.getenv("CITEPAPER_LLM_TIMEOUT", "45"))
        if not 1 <= timeout <= 300:
            raise ValueError("CITEPAPER_LLM_TIMEOUT must be between 1 and 300 seconds")
        return cls(
            database=Path(os.getenv("CITEPAPER_DATABASE", ".citepaper/citepaper.sqlite3")),
            api_keys=keys,
            embedded_worker=os.getenv("CITEPAPER_EMBEDDED_WORKER", "0") == "1",
            llm_base_url=base,
            llm_model=model,
            llm_api_key=os.getenv("CITEPAPER_LLM_API_KEY", ""),
            llm_timeout=timeout,
            allow_arxiv=os.getenv("CITEPAPER_ALLOW_ARXIV", "0") == "1",
        )
