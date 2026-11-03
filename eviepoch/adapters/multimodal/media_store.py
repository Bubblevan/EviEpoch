"""Namespace-scoped media bytes on local filesystem (TRD §20.3)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def asset_path(root: Path, ns: str, content_hash: str, mime: str) -> Path:
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".bin")
    return root / ns / content_hash[:2] / f"{content_hash}{ext}"


def write_asset(root: Path, ns: str, content_hash: str, mime: str, raw: bytes) -> Path:
    digest = hashlib.sha256(raw).hexdigest()
    if digest != content_hash or not raw:
        raise ValueError("media hash or length does not match decoded bytes")
    path = asset_path(root, ns, content_hash, mime)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if len(existing) != len(raw) or hashlib.sha256(existing).hexdigest() != content_hash:
            raise ValueError("media hash collision at storage path")
        return path
    temporary = path.with_name(path.name + ".staging")
    temporary.write_bytes(raw)
    stored = temporary.read_bytes()
    try:
        if len(stored) != len(raw) or hashlib.sha256(stored).hexdigest() != content_hash:
            raise ValueError("stored media failed hash or length check")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def read_asset(path: Path) -> bytes:
    return path.read_bytes()
