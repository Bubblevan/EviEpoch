"""Typed failures shared by provider adapters and retrieval fallback."""
from __future__ import annotations

class ProviderUnavailable(RuntimeError):
    """A required model stage failed; partial Add must not be reported as success."""

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage

class StructuredOutputTruncated(ProviderUnavailable):
    """A bounded completion ran out of output room; callers may split the input."""

class StructuredOutputInvalid(ProviderUnavailable):
    """A completed response was not a JSON object; ingestion may split boundedly."""
