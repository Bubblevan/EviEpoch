"""Explicit local research configuration; never silently switches competition models."""
from __future__ import annotations

import os
from dataclasses import replace

from dotenv import load_dotenv

from eviepoch.config import Settings


def load(*, deepseek: bool = False) -> Settings:
    load_dotenv()
    config = Settings.from_env()
    if not deepseek:
        return config
    return replace(config, model_policy="research", llm_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                   llm_base_url=os.getenv("EPOCH_RESEARCH_LLM_BASE_URL", "https://api.deepseek.com"),
                   llm_model=os.getenv("EPOCH_RESEARCH_LLM_MODEL", "deepseek-flash"),
                   embed_api_key=config.embed_api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                   embed_base_url=config.embed_base_url or os.getenv("DASHSCOPE_BASE_URL", ""))
