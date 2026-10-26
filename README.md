# EviEpoch

A small, executable memory-retrieval experiment. One pipeline covers Add, Search, and a local replay interface; the few exposed parameters are the ones that change retrieval behavior.

## Pipeline

1. Add keeps each non-empty conversation turn verbatim, with its timestamp and speaker.
2. The language model extracts atomic first-person facts from the same chunk. Facts supplement the turns; they never replace them.
3. Both channels are embedded and committed together. A repeated `request_id` for the same `user_id` is a no-op.
4. Search embeds the query and a first-person recall question. It fuses cosine similarity with `EPOCH_RECALL_WEIGHT` (default `0.5`).
5. Selection deduplicates evidence, may add the adjacent turns from the same chunk, applies count/character/token budgets, then puts verbatim turns before extracted facts while preserving relevance within each group.
6. Search returns evidence only and is always scoped to one `user_id`.

The framework keeps one default path and lets experiments vary the recall weight, result limit, neighbor radius, and raw-first ordering. Production admission controls, diagnostic modes, and unrelated experimental branches are not part of this small runner.

## Run

Copy `.env.example` to `.env`, then fill `EPOCH_EMBED_API_KEY`, `EPOCH_EMBED_BASE_URL`, and `EPOCH_AUTH_TOKEN`. Add `OPENAI_API_KEY` to enable fact extraction and recall-question rewriting; without it, the framework stores raw turns and searches with the original query. Use an embedding endpoint that matches the embedding key's region. Start with:

```powershell
docker compose --env-file .env -p eviepoch up -d --build
```

The service binds to `127.0.0.1:8012`; SQLite data persists in the `eviepoch-data` volume. For local development, install `requirements.txt` and run `python -m uvicorn eviepoch.api:app --env-file .env --host 127.0.0.1 --port 8000`.

## API

`GET /health` is an unauthenticated liveness check. Add and Search require the configured token. Supported headers are `Authorization: Bearer <token>`, `Authorization: Token <token>`, and `X-API-Key: <token>`.

```json
{"request_id":"r1","user_id":"u1","session_id":"s1","messages":[{"role":"user","content":"I moved to Kyoto.","timestamp":1684540800000}]}
```

```json
{"query":"Where did I move?","user_id":"u1","top_k":20,"options":[]}
```

Add returns its request/user/session identifiers after the SQLite commit. Search returns `{"data":[{"id":"...","content":"...","score":0.0,"created_at":"..."}]}`. Timestamp values may be Unix seconds or milliseconds.

## Experiments

`experiments/replay.py` accepts a JSONL stream of Add and Search requests and reports evidence recall at the requested `top_k`. Add rows have `{"op":"add","payload":{...}}`; Search rows may include `gold_ids` beside `payload` for evaluation. Keep private evaluation data outside Git. A sample is in `experiments/replay.example.jsonl`.

Run the local contract tests with `python -m unittest discover -s tests`.

```powershell
python experiments/replay.py experiments/replay.example.jsonl --url http://127.0.0.1:8012
```
