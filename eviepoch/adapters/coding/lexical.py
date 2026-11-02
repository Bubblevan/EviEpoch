"""Exact/sparse tokens for code paths, symbols, and test artifacts."""
from __future__ import annotations

import re

_PATH = re.compile(
    r"(?<![\w./-])([\w.-]+(?:/[\w.-]+)+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|cs|cpp|c|h|hpp|rb|php|swift|md|yaml|yml|toml|json|sql))(?![\w./-])",
    re.I,
)
_SYMBOL = re.compile(r"\b(?:def|class|function|func|type|interface|struct|enum)\s+([A-Za-z_][\w.]*)")
_SHA = re.compile(r"\b([0-9a-f]{7,40})\b", re.I)
_EXIT = re.compile(r"(?:exit(?:\s*code)?|returned)\s*[:=]?\s*(-?\d+)", re.I)
_EXCEPTION = re.compile(r"\b([A-Z][a-zA-Z0-9_]+(?:Error|Exception))\b")
_TEST = re.compile(r"\b(?:FAILED|PASSED|ERROR)\s+([\w./:-]+)")
_REPO = re.compile(r"(?:repo(?:sitory)?|remote|project)\s*[:=]\s*(\S+)", re.I)
_COMMIT = re.compile(r"(?:commit|sha|tree)\s*[:=]\s*([0-9a-f]{7,40})", re.I)


def coding_terms(text: str) -> list[str]:
    terms: list[str] = []
    for pattern in (_PATH, _SYMBOL, _SHA, _EXIT, _EXCEPTION, _TEST, _REPO, _COMMIT):
        for match in pattern.finditer(text):
            value = match.group(1)
            if value and value not in terms:
                terms.append(value)
    return terms[:256]


def index_body(*parts: str) -> str:
    return "\n".join(part for part in parts if part)
