"""Bounded, disk-backed capture of public Add/Search payload bodies.

Authorization headers, client addresses, and unrelated routes are never recorded.
Set EPOCH_AUDIT_DIR to enable capture.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()
_WRITE_ERROR_REPORTED = False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PayloadAuditMiddleware:
    """Capture up to a fixed number of request/response bytes for POST /add and /search."""

    def __init__(self, app, audit_dir: str = "", max_body_bytes: int = 1_048_576,
                 max_disk_bytes: int = 2_147_483_648, retention_days: int = 30):
        self.app = app
        self.audit_dir = Path(audit_dir) if audit_dir else None
        self.max_body_bytes = max(1, int(max_body_bytes))
        self.max_disk_bytes = max(1, int(max_disk_bytes))
        self.retention_days = max(1, int(retention_days))

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (self.audit_dir is None or scope.get("type") != "http"
                or scope.get("method") != "POST" or path not in {"/add", "/search"}):
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        request_data = bytearray()
        response_data = bytearray()
        request_truncated = False
        response_truncated = False
        status_code = 500

        async def capture_receive():
            nonlocal request_truncated
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                available = self.max_body_bytes - len(request_data)
                if len(chunk) > available:
                    request_truncated = True
                if available > 0:
                    request_data.extend(chunk[:available])
            return message

        async def capture_send(message):
            nonlocal status_code, response_truncated
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            elif message.get("type") == "http.response.body":
                chunk = message.get("body", b"")
                available = self.max_body_bytes - len(response_data)
                if len(chunk) > available:
                    response_truncated = True
                if available > 0:
                    response_data.extend(chunk[:available])
            await send(message)

        try:
            await self.app(scope, capture_receive, capture_send)
        finally:
            await self._write(
                path=path,
                status=status_code,
                started=started,
                request_body=bytes(request_data).decode("utf-8", errors="replace"),
                response_body=bytes(response_data).decode("utf-8", errors="replace"),
                request_truncated=request_truncated,
                response_truncated=response_truncated,
            )

    async def _write(self, *, path, status, started, request_body, response_body,
                     request_truncated, response_truncated):
        record = {
            "timestamp": _iso_now(),
            "method": "POST",
            "path": path,
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "request_body": request_body,
            "request_truncated": request_truncated,
            "response_body": response_body,
            "response_truncated": response_truncated,
        }
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        await asyncio.to_thread(self._append, line)

    def _append(self, line: bytes):
        global _WRITE_ERROR_REPORTED
        folder = self.audit_dir
        if folder is None:
            return
        try:
            with _WRITE_LOCK:
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    os.chmod(folder, 0o700)
                now = datetime.now(timezone.utc)
                files = sorted(folder.glob("api-requests-*.jsonl"), key=lambda p: p.name)
                cutoff = now.timestamp() - self.retention_days * 86400
                for item in files:
                    try:
                        if item.stat().st_mtime < cutoff:
                            item.unlink()
                    except FileNotFoundError:
                        pass
                files = sorted(folder.glob("api-requests-*.jsonl"), key=lambda p: p.name)
                total = sum(item.stat().st_size for item in files if item.exists())
                if total + len(line) > self.max_disk_bytes:
                    marker = folder / "AUDIT_CAP_REACHED.txt"
                    if not marker.exists():
                        marker.write_text(f"{_iso_now()} payload audit paused: disk byte limit reached.\n", encoding="utf-8")
                        if os.name != "nt":
                            os.chmod(marker, 0o600)
                        log.error("payload audit disk cap reached; capture paused")
                    return
                marker = folder / "AUDIT_CAP_REACHED.txt"
                if marker.exists():
                    marker.unlink()
                    log.info("payload audit resumed after disk usage fell below its cap")
                current = folder / f"api-requests-{now:%Y-%m-%d}.jsonl"
                with current.open("ab") as stream:
                    if os.name != "nt":
                        os.chmod(current, 0o600)
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            if not _WRITE_ERROR_REPORTED:
                _WRITE_ERROR_REPORTED = True
                log.error("payload audit write failed (%s)", type(exc).__name__)
