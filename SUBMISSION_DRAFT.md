# EviEpoch — AML submission draft

## Form fields

- **System name:** EviEpoch
- **Version:** v0.1.0 (code commit `4e7a614b47286241d94330a7c4329c660de45561`)
- **Add API URL:** https://bubblevan.bbroot.com/v1/memories/add
- **Search API URL:** https://bubblevan.bbroot.com/v1/memories/search
- **Authentication:** `Authorization: Token <Memory System Key>`
- **Public repository:** https://github.com/Bubblevan/EviEpoch
- **Applicant:** Bao Bowen (包博文), individual

## Submission notes / run instructions

EviEpoch is a synchronous, evidence-returning textual memory service. Add stores the original conversation turns and timestamps, supplements them with extracted atomic facts, and returns success with the submitted request, user, and session IDs after persistence. Repeating the same `request_id` for a `user_id` is idempotent. The configured language model is `gpt-4o-mini`; embeddings use `text-embedding-v4` at 1024 dimensions.

Search accepts `query`, `user_id`, and `top_k`, and returns ranked evidence in `{"data": [...]}`. Retrieval is isolated by `user_id`; Search returns source evidence and does not generate a final answer. The public service uses HTTPS and requires the Memory System Key on Add and Search. `GET /health` is an unauthenticated liveness check.

The deployment runs one Uvicorn worker with persistent SQLite storage. A reproducible synthetic test of the same API image completed 200 Add and 200 Search requests at concurrency 1, 4, 8, 16, and 32 with HTTP 200 throughout, using local fake providers and a temporary database. At concurrency 16, the 400-memory profile measured p95 Add 24 ms and Search 551 ms. On a separate 10,000-memory, 1024-dimensional profile, Search p95 was 191 ms at concurrency 1, 1.66 s at 4, 3.25 s at 8, and 5.67 s at 16. These timings isolate the API and SQLite retrieval path; the configured model providers and network add their own latency and rate limits. The initial evaluation target is 16 concurrent Add and 16 concurrent Search requests.

For a self-hosted run, provide the model and embedding credentials plus `EPOCH_AUTH_TOKEN` through environment variables, then run `docker compose --env-file .env -p eviepoch up -d --build`. The container listens on port 8000, persists its SQLite database in `/data`, and uses an `unless-stopped` restart policy. Run the repository contract tests with `python -m unittest discover -s tests`.

## Method and provenance disclosure

The implementation retains verbatim conversation turns as source evidence and augments them with extracted atomic facts. Search combines query-vector and recall-question-vector similarity, deduplicates evidence, can include adjacent turns from the same chunk, applies result budgets, and returns evidence only. The published repository and pinned code commit above identify the submitted implementation and its method changes.

Upstream provenance: ActiveMemoryIndex (AMI), `academic-v4-20261016-rc6`, upstream commit `2754a7880a169a7dd60cfc7f8d279454885f52a9`; original author Xuhao Lin. The upstream MIT license and attribution are retained in the EviEpoch repository. AMI's upstream capabilities are credited as upstream work and are not claimed here as new contributions.
