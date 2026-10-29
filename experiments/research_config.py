"""Explicit local research configuration; never silently switches competition models."""
from __future__ import annotations

import os
from dataclasses import replace

from dotenv import load_dotenv

from eviepoch.config import Settings


def load(*, deepseek: bool = False, codex_luna: bool = False) -> Settings:
    load_dotenv()
    config = Settings.from_env()
    if deepseek and codex_luna:
        raise ValueError('Choose exactly one research model backend')
    if codex_luna:
        return replace(config, model_policy='research', llm_model='gpt-6-luna',
                       llm_api_key='codex-cli-auth-not-an-api-key', llm_base_url='',
                       embed_api_key=config.embed_api_key or os.getenv('DASHSCOPE_API_KEY', ''),
                       embed_base_url=config.embed_base_url or os.getenv('DASHSCOPE_BASE_URL', ''))
    if not deepseek:
        return config
    return replace(config, model_policy="research", llm_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                   llm_base_url=os.getenv("EPOCH_RESEARCH_LLM_BASE_URL", "https://api.deepseek.com"),
                   llm_model=os.getenv("EPOCH_RESEARCH_LLM_MODEL", "deepseek-flash"),
                   embed_api_key=config.embed_api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                   embed_base_url=config.embed_base_url or os.getenv("DASHSCOPE_BASE_URL", ""))


def make_provider(config, *, codex_luna=False, codex_max_calls=32, codex_cache_dir=None):
    if codex_luna:
        from experiments.codex_provider import CodexCLIProviders
        return CodexCLIProviders(config, max_calls=codex_max_calls, cache_dir=codex_cache_dir)
    from eviepoch.providers import Providers
    return Providers(config)
