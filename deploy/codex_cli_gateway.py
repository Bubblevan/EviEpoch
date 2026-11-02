"""Loopback-only HTTP adapter from EviEpoch Chat Completions to Codex CLI Luna.

This is an internal model gateway. Run it on the authenticated laptop and make
it reachable from ECS only through the restricted reverse SSH tunnel.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from eviepoch.config import Settings
from eviepoch.providers import ProviderUnavailable, StructuredOutputInvalid
from experiments.codex_provider import CodexCLIProviders, MODEL

MAX_REQUEST_BYTES = 1_048_576
MAX_OUTPUT_TOKENS = 12_000


def _error(status: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=status,
        headers=headers,
        content={"error": {"message": message, "type": "server_error", "code": code}},
    )


def create_app(*, token_file: Path, max_calls: int = 256, call_timeout: float = 180,
               max_pending: int = 32) -> FastAPI:
    token = token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("Gateway token file must contain at least 32 characters")

    config = Settings(
        model_policy="research",
        llm_model=MODEL,
        # The Codex CLI uses its own existing login; this placeholder is never
        # passed to the CLI subprocess or used for API-key billing.
        llm_api_key="codex-cli-authenticated",
    )
    provider = CodexCLIProviders(
        config,
        cache_dir=ROOT / "logs" / "codex-cli-cache",
        max_calls=max_calls,
        timeout=call_timeout,
    )
    queue = asyncio.Semaphore(1)
    state_lock = asyncio.Lock()
    accepted = 0
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        nonlocal accepted
        supplied = request.headers.get("authorization", "")
        scheme, _, credential = supplied.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, token):
            return _error(401, "unauthorized", "Invalid gateway credential")

        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                if int(raw_length) > MAX_REQUEST_BYTES:
                    return _error(413, "request_too_large", "Request exceeds the internal gateway limit")
            except ValueError:
                return _error(400, "invalid_content_length", "Invalid Content-Length")
        body = await request.body()
        if len(body) > MAX_REQUEST_BYTES:
            return _error(413, "request_too_large", "Request exceeds the internal gateway limit")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return _error(400, "invalid_json", "Request body must be valid JSON")
        if not isinstance(payload, dict) or payload.get("model") != MODEL:
            return _error(400, "invalid_model", "The internal gateway only accepts gpt-6-luna")
        if payload.get("stream", False):
            return _error(400, "stream_not_supported", "Streaming is not enabled on this internal route")
        if payload.get("tools") or payload.get("functions"):
            return _error(400, "tools_not_supported", "Tool calls are disabled on this internal route")
        messages = payload.get("messages")
        if (not isinstance(messages, list) or len(messages) != 2
                or any(not isinstance(message, dict) for message in messages)):
            return _error(400, "invalid_messages", "Expected one system message and one user message")
        if (messages[0].get("role") != "system" or messages[1].get("role") != "user"
                or not isinstance(messages[0].get("content"), str)
                or not isinstance(messages[1].get("content"), str)):
            return _error(400, "invalid_messages", "Expected text-only system and user messages")
        max_tokens = payload.get("max_tokens", MAX_OUTPUT_TOKENS)
        if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
            return _error(400, "invalid_output_budget", "max_tokens must be between 1 and 6000")
        response_format = payload.get("response_format")
        if response_format is not None and (
            not isinstance(response_format, dict) or response_format.get("type") != "json_object"
        ):
            return _error(400, "unsupported_response_format", "Only JSON-object mode is supported")
        structured = response_format is not None
        system = messages[0]["content"]
        if not structured:
            system += (
                '\nReturn exactly one JSON object with a single string field named "text". '
                'Put the requested plain-text completion in that field.'
            )

        async with state_lock:
            if accepted >= max_pending:
                return _error(429, "gateway_queue_full", "The local Codex queue is full", retry_after=5)
            accepted += 1
        try:
            async with queue:
                before_calls = provider.usage["codex_calls"]
                before_input = provider.usage["input_tokens"]
                before_output = provider.usage["output_tokens"]
                try:
                    result = await asyncio.to_thread(
                        provider._structured,
                        system,
                        messages[1]["content"],
                        max_tokens,
                        "ecs_gateway",
                    )
                except StructuredOutputInvalid:
                    logging.getLogger("codex_gateway").warning("Rejected invalid structured CLI output")
                    return _error(502, "invalid_model_output", "Codex CLI returned invalid structured output")
                except ProviderUnavailable as exc:
                    # Do not log exception text: it can contain user-supplied input.
                    logging.getLogger("codex_gateway").warning(
                        "Codex inference unavailable (%s)", type(exc).__name__
                    )
                    return _error(503, "codex_unavailable", "Codex CLI inference is unavailable", retry_after=5)
                except Exception as exc:
                    logging.getLogger("codex_gateway").error(
                        "Unexpected gateway failure (%s)", type(exc).__name__
                    )
                    return _error(503, "gateway_unavailable", "Internal model gateway is unavailable", retry_after=5)

                if structured:
                    content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                else:
                    if set(result) != {"text"} or not isinstance(result["text"], str):
                        logging.getLogger("codex_gateway").warning("Rejected invalid plain-text CLI output")
                        return _error(502, "invalid_model_output", "Codex CLI returned invalid text output")
                    content = result["text"]
                if len(content.encode("utf-8")) > MAX_REQUEST_BYTES:
                    return _error(502, "output_too_large", "Structured model output exceeds the gateway limit")
                usage = {
                    "prompt_tokens": provider.usage["input_tokens"] - before_input,
                    "completion_tokens": provider.usage["output_tokens"] - before_output,
                }
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                # Cache hits correctly report zero newly consumed CLI tokens.
                return JSONResponse(content={
                    "id": "chatcmpl-" + uuid.uuid4().hex,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": MODEL,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }],
                    "usage": usage,
                    "x_codex_cli_calls": provider.usage["codex_calls"] - before_calls,
                })
        finally:
            async with state_lock:
                accepted -= 1

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18123)
    parser.add_argument("--max-calls", type=int, default=256)
    parser.add_argument("--call-timeout", type=float, default=180)
    parser.add_argument("--max-pending", type=int, default=32)
    parser.add_argument("--token-file", type=Path, default=ROOT / "logs" / "codex-gateway-token.txt")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("The model gateway must bind to loopback only")
    if args.max_calls <= 0 or args.call_timeout <= 0 or args.max_pending <= 0:
        parser.error("Call limits must be positive")

    log_path = ROOT / "logs" / "codex-gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn
    logging.getLogger("codex_gateway").info(
        "Starting loopback model gateway; model=%s max_calls=%d max_pending=%d",
        MODEL, args.max_calls, args.max_pending,
    )
    uvicorn.run(create_app(token_file=args.token_file, max_calls=args.max_calls,
                           call_timeout=args.call_timeout, max_pending=args.max_pending),
                host=args.host, port=args.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
