"""Task adapters: textual (default), coding, and multimodal."""
from __future__ import annotations

from ..config import Settings
from .base import TaskAdapter
from .textual import TextualAdapter


def get_task_adapter(config: Settings) -> TaskAdapter:
    track = getattr(config, "task_adapter", "textual")
    if track == "textual":
        return TextualAdapter(config)
    if track == "coding":
        from .coding.adapter import CodingAdapter
        return CodingAdapter(config)
    if track == "multimodal":
        from .multimodal.adapter import MultimodalAdapter
        return MultimodalAdapter(config)
    raise ValueError(f"Unknown EPOCH_TASK_ADAPTER: {track}")
