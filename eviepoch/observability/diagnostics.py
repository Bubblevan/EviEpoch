"""Content-free structured diagnostics for API and provider failures."""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("eviepoch.diagnostics")
_REQUEST_CONTEXT = contextvars.ContextVar("eviepoch_request_diagnostics", default={})
_TRACE_WRITE_LOCK = threading.Lock()
_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_STATUS = re.compile(r"^[1-5][0-9]{2}$")
_RETRY_SECONDS = re.compile(r"^[0-9]{1,5}$")

_SAFE_REASON_CODES = {
    "quote is absent from the authoritative source": "quote_not_in_source",
    "ambiguous quote; supply a larger unique quote": "quote_ambiguous",
    "invalid quote occurrence": "quote_occurrence_invalid",
    "message ordinal is invalid": "message_ordinal_invalid",
    "extraction input exceeds the declared token budget": "extraction_input_budget",
    "extraction exceeds the declared object budget": "extraction_object_budget",
    "bounded extraction budget exhausted": "extraction_call_budget",
    "structured completion did not finish normally": "completion_finish_reason",
    "candidate verification returned invalid IDs": "candidate_verification_ids",
    "embedding service unavailable": "embedding_provider_failure",
    "configured reranker unavailable": "reranker_provider_failure",
    "structured completion contained invalid JSON": "completion_invalid_json",
    "memory changed during bounded retrieval retries": "read_consistency_changed",
}


def _safe_label(value, fallback="unknown") -> str:
    if isinstance(value, str) and _LABEL.fullmatch(value):
        return value
    return fallback


def _status_code(value):
    if type(value) is int and 100 <= value <= 599:
        return value
    if isinstance(value, str) and _STATUS.fullmatch(value):
        return int(value)
    return None


def _exception_context(error) -> dict:
    causes = []
    provider_status = _status_code(getattr(error, "status_code", None))
    retry_after = None
    current = error.__cause__ or error.__context__
    seen = {id(error)}
    for _ in range(5):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        causes.append(_safe_label(type(current).__name__, "exception"))
        status = _status_code(getattr(current, "status_code", None))
        response = getattr(current, "response", None)
        if status is None:
            status = _status_code(getattr(response, "status_code", None))
        if provider_status is None and status is not None:
            provider_status = status
        headers = getattr(response, "headers", None)
        if retry_after is None and headers is not None:
            value = headers.get("retry-after")
            if isinstance(value, str) and _RETRY_SECONDS.fullmatch(value.strip()):
                seconds = int(value.strip())
                if seconds <= 3600:
                    retry_after = seconds
        current = current.__cause__ or current.__context__
    return {
        "cause_types": causes,
        "provider_status_code": provider_status,
        "provider_retry_after_seconds": retry_after,
    }


def _error_code(error) -> str:
    args = getattr(error, "args", ())
    message = args[0] if len(args) == 1 and isinstance(args[0], str) else None
    if message in _SAFE_REASON_CODES:
        return _SAFE_REASON_CODES[message]
    name = type(error).__name__
    if name in {"JSONDecodeError", "ValueError"}:
        return "invalid_provider_response"
    status = _status_code(getattr(error, "status_code", None))
    if status == 429:
        return "provider_rate_limited"
    if status in {401, 403}:
        return "provider_auth_rejected"
    if status is not None and status >= 500:
        return "provider_server_error"
    if name == "InvalidExtraction":
        return "invalid_extraction"
    if name == "IngestBusy":
        return "admission_limit"
    if name in {"ProviderUnavailable", "StructuredOutputTruncated", "StructuredOutputInvalid"}:
        return "provider_unavailable"
    return "request_failure"


def _fingerprint(value, key) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    secret = key.encode("utf-8") if isinstance(key, str) and key else b"eviepoch-request-log-v1"
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


@contextmanager
def request_context(*, operation: str, request_id: str | None = None, fingerprint_key: str = "",
                    message_count: int | None = None, input_chars: int | None = None,
                    query_chars: int | None = None, top_k: int | None = None,
                    options_count: int | None = None):
    """Attach allow-listed, content-free request metadata to downstream stage logs."""
    context = dict(_REQUEST_CONTEXT.get())
    context.update({"operation": _safe_label(operation),
                    "trace_id": context.get("trace_id") or uuid.uuid4().hex})
    fingerprint = _fingerprint(request_id, fingerprint_key)
    if fingerprint is not None:
        context["request_id_fingerprint"] = fingerprint
    for key, value in (("message_count", message_count), ("input_chars", input_chars),
                       ("query_chars", query_chars), ("top_k", top_k),
                       ("options_count", options_count)):
        if type(value) is int and value >= 0:
            context[key] = value
    token = _REQUEST_CONTEXT.set(context)
    try:
        yield
    finally:
        _REQUEST_CONTEXT.reset(token)


def begin_request_trace():
    """Start an authorized API trace shared by request audit and internal stages."""
    trace_id = uuid.uuid4().hex
    token = _REQUEST_CONTEXT.set({"trace_id": trace_id})
    return trace_id, token


def end_request_trace(token) -> None:
    _REQUEST_CONTEXT.reset(token)


def _trace_default(value):
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"unsupported trace value: {type(value).__name__}")


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def log_dataflow_event(stage: str, **fields) -> None:
    """Append private, bounded, content-bearing Add/Search stage data to disk."""
    if os.getenv("EPOCH_PIPELINE_TRACE_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    folder_value = os.getenv("EPOCH_PIPELINE_TRACE_DIR") or os.getenv("EPOCH_AUDIT_DIR")
    if not folder_value:
        return
    folder = Path(folder_value)
    context = _REQUEST_CONTEXT.get()
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "event": "pipeline_dataflow",
        "trace_id": context.get("trace_id"),
        "operation": context.get("operation"),
        "stage": _safe_label(stage),
        **fields,
    }
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=_trace_default) + "\n").encode("utf-8")
    max_event = _positive_env("EPOCH_PIPELINE_TRACE_MAX_EVENT_BYTES", 4_194_304)
    if len(line) > max_event:
        import hashlib

        line = (json.dumps({
            "timestamp": event["timestamp"], "event": event["event"],
            "trace_id": event["trace_id"], "operation": event["operation"],
            "stage": event["stage"], "payload_omitted": True,
            "payload_bytes": len(line), "payload_sha256": hashlib.sha256(line).hexdigest(),
        }, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with _TRACE_WRITE_LOCK:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                os.chmod(folder, 0o700)
            now = datetime.now(timezone.utc)
            files = sorted(folder.glob("pipeline-traces-*.jsonl"), key=lambda item: item.name)
            cutoff = now.timestamp() - _positive_env("EPOCH_PIPELINE_TRACE_RETENTION_DAYS", 30) * 86400
            for item in files:
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                except FileNotFoundError:
                    pass
            files = sorted(folder.glob("pipeline-traces-*.jsonl"), key=lambda item: item.name)
            total = sum(item.stat().st_size for item in files if item.exists())
            if total + len(line) > _positive_env("EPOCH_PIPELINE_TRACE_MAX_DISK_BYTES", 4_294_967_296):
                marker = folder / "PIPELINE_TRACE_CAP_REACHED.txt"
                if not marker.exists():
                    marker.write_text(f"{event['timestamp']} pipeline trace paused: disk byte limit reached.\n",
                                      encoding="utf-8")
                    if os.name != "nt":
                        os.chmod(marker, 0o600)
                return
            marker = folder / "PIPELINE_TRACE_CAP_REACHED.txt"
            if marker.exists():
                marker.unlink()
            current = folder / f"pipeline-traces-{now:%Y-%m-%d}.jsonl"
            with current.open("ab") as stream:
                if os.name != "nt":
                    os.chmod(current, 0o600)
                stream.write(line)
                stream.flush()
    except OSError as exc:
        log.error("pipeline trace write failed (%s)", type(exc).__name__)


def _context_fields() -> dict:
    # Context is constructed only from the allow-list above; never add request bodies or IDs.
    return dict(_REQUEST_CONTEXT.get())


def log_api_failure(*, operation: str, error, status_code: int, duration_ms: float,
                    request_id: str | None = None, fingerprint_key: str = "",
                    message_count: int | None = None, input_chars: int | None = None,
                    query_chars: int | None = None, top_k: int | None = None,
                    options_count: int | None = None) -> None:
    stage = getattr(error, "stage", None)
    if not stage and type(error).__name__ == "IngestBusy":
        stage = "admission"
    event = {
        "event": "api_request_failure",
        "operation": operation,
        "status_code": status_code,
        "duration_ms": round(max(0.0, float(duration_ms)), 2),
        "error_type": _safe_label(type(error).__name__, "exception"),
        "error_code": _error_code(error),
        **_context_fields(),
        "stage": _safe_label(stage),
        **_exception_context(error),
    }
    fingerprint = _fingerprint(request_id, fingerprint_key)
    if fingerprint is not None:
        event["request_id_fingerprint"] = fingerprint
    for key, value in (
        ("message_count", message_count),
        ("input_chars", input_chars),
        ("query_chars", query_chars),
        ("top_k", top_k),
        ("options_count", options_count),
    ):
        if type(value) is int and value >= 0:
            event[key] = value
    log.warning("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def log_provider_failure(*, stage: str | None, error, duration_ms: float) -> None:
    event = {
        "event": "provider_stage_failure",
        "stage": _safe_label(stage, "structured"),
        "duration_ms": round(max(0.0, float(duration_ms)), 2),
        "provider_exception_type": _safe_label(type(error).__name__, "exception"),
        "error_code": _error_code(error),
        **_context_fields(),
        **_exception_context(error),
    }
    log.warning("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def log_pipeline_failure(*, stage: str, error) -> None:
    """Log a failed deterministic validation stage without including its message."""
    event = {
        "event": "pipeline_stage_failure",
        "stage": _safe_label(stage, "unknown"),
        "error_type": _safe_label(type(error).__name__, "exception"),
        "error_code": _error_code(error),
        **_context_fields(),
    }
    log.warning("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def log_ingest_degraded(*, error, source_count: int) -> None:
    """Record source-only publication without exposing message content or IDs."""
    event = {
        "event": "ingest_degraded",
        "stage": _safe_label(getattr(error, "stage", None), "extract"),
        "error_code": _error_code(error),
        "source_count": max(0, int(source_count)),
        **_context_fields(),
    }
    log.warning("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def log_search_result(trace) -> None:
    """Counts and stage timings only; IDs, paths, query and content never leave the trace."""
    event = {"event": "search_selection", **_context_fields()}
    for field in ("binding_status", "selection_status"):
        event[field] = _safe_label(trace.get(field))
    for field in ("candidates", "token_cost", "token_budget", "consistency_retries"):
        value = trace.get(field)
        if type(value) is int:
            event[field] = value
    for field in ("selected_ids", "covered_obligations", "missing_obligations"):
        event[field + "_count"] = len(trace.get(field, []))
    event["timings_ms"] = {k: round(v, 2) for k, v in trace.get("timings_ms", {}).items()
                           if _LABEL.fullmatch(k) and type(v) in {int, float} and 0 <= v < 1e9}
    log.info("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def log_provider_success(*, stage, duration_ms):
    event = {"event": "provider_stage_success", "stage": _safe_label(stage, "structured"),
             "duration_ms": round(max(0.0, float(duration_ms)), 2), **_context_fields()}
    log.info("%s", json.dumps(event, sort_keys=True, separators=(",", ":")))
