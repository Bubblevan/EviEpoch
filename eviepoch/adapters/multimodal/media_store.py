"""Namespace-scoped media bytes on local filesystem (TRD §20.3)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def asset_path(root: Path, ns: str, content_hash: str, mime: str) -> Path:
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".bin")
    return root / ns / content_hash[:2] / f"{content_hash}{ext}"


def write_asset(root: Path, ns: str, content_hash: str, mime: str, raw: bytes) -> Path:
    path = asset_path(root, ns, content_hash, mime)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != content_hash:
        raise ValueError("media hash collision at storage path")
    return path


def read_asset(path: Path) -> bytes:
    return path.read_bytes()
