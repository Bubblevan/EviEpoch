# Deployment record

This is a historical record of the two deployments as observed on 2026-10-30. It does not describe the current repository's production defaults: new standard Compose deployments require PostgreSQL/pgvector; the SQLite adapters remain available only for explicit local verification. The existing remote instances are not changed by this repository update.

Updated: 2026-10-30 (Asia/Shanghai)

The two EviEpoch endpoints below are independent instances. They have separate SQLite databases and audit directories.

## Instance at bubblevan.de5.net

- Add: POST https://bubblevan.de5.net/v1/memories/add
- Search: POST https://bubblevan.de5.net/v1/memories/search
- Health: GET https://bubblevan.de5.net/v1/memories/health
- Authentication: Authorization: Bearer <Memory System Key>
- Runtime source marker: 1bfd011872374ec98b7885c9b1cbe23801f68a5f
- LLM: deepseek-flash. Embeddings: text-embedding-v4, 1024 dimensions.
- The app runs under systemd and listens only on 127.0.0.1:8012 behind Caddy HTTPS.
- Configured concurrency: 16 Add and 16 Search; 1.5 CPU cores and a 700 MiB memory limit.
- SQLite: /var/lib/eviepoch/eviepoch-v1.sqlite3. Payload audit: /var/lib/eviepoch/audit.

## Instance at bubblevan.bbroot.com

- Add: POST https://bubblevan.bbroot.com/v1/memories/add
- Search: POST https://bubblevan.bbroot.com/v1/memories/search
- Health: GET https://bubblevan.bbroot.com/v1/memories/health (the existing /health path remains available)
- Authentication: Authorization: Bearer <the key already configured in this instance's private .env>
- Runtime deployment commit: 26a9afd1ed2689823fa2892b489ac67a3c2a3bac. The application is a Docker Compose service named eviepoch-ecs-eviepoch-1.
- LLM: deepseek-flash at `https://api.deepseek.com`, with the key sourced from the private `.env` through `deploy/compose.deepseek.yaml`. Embeddings: text-embedding-v4, 1024 dimensions. Profile: full; model policy: research.
- The container listens on host loopback 127.0.0.1:8012 behind the existing Caddy HTTPS site. The container port is not publicly bound.
- Configured concurrency: 16 Add and 16 Search; Docker limits the service to 1.5 CPU cores and 700 MiB RAM. Docker JSON logs rotate at 10 MiB per file, with three files retained.
- SQLite is preserved in Docker volume eviepoch-ecs_eviepoch-data at /data/eviepoch-v1.sqlite3.
- Authenticated Add/Search request and response bodies are recorded under /var/lib/eviepoch/audit. The directory is mode 0700 and files are mode 0600. Capture is capped at 1 MiB per request and response, 8 GiB total, and 30 days. Authorization headers and unauthenticated requests are not recorded.
- A database backup made before the audit upgrade is at /var/backups/eviepoch-bbroot-20261029/eviepoch-v1-pre-audit.sqlite3. The prior image is retained as eviepoch-ecs/eviepoch:pre-audit-20261029.
- Caddy configuration was backed up in the same backup directory. Existing /health and Add/Search routes were preserved; the versioned health path was added.

## Verification on the .com instance

- External HTTPS health checks for /health and /v1/memories/health returned HTTP 200 with certificate verification successful.
- Unauthenticated Add and Search checks returned HTTP 401.
- One synthetic provider-backed Add and Search through the application returned HTTP 200; Search returned the matching synthetic marker.
- An authenticated top_k=0 Search through the Caddy domain route returned HTTP 200 without a model call.
- The initial audit export contained 3 synthetic records; after the earlier 4-way sample it contained 11. The latest export after the public 8-way and 16-way tests and Token recheck contains 63 records: Add 200: 31 and Search 200: 32. All records are synthetic provider probes or load-test data; no official Smoke payload was captured in this test round.
- The earlier 4-way sample exercised the Caddy TLS listener locally. The later 8-way and 16-way tests were sent from the Windows workstation through the public HTTPS hostname, used unique synthetic users, top_k=100, and no client retries.
- At 8 concurrent Add plus 8 concurrent Search requests (16 simultaneous clients), all 16 returned HTTP 200; all 8 searches matched the seeded synthetic evidence. Add p50 19.47 s, p95/p99 21.62 s; Search p50 9.98 s, p95/p99 18.14 s; total wave 21.65 s.
- At 16 concurrent Add plus 16 concurrent Search requests (32 simultaneous clients), all 32 returned HTTP 200; all 16 searches matched the seeded evidence. Add p50 35.47 s, p95/p99 35.97 s; Search p50 16.99 s, p95/p99 33.22 s; total wave 35.98 s.
- After the 16-way wave, the healthy container had zero restarts, cgroup current memory 189,435,904 bytes and peak 200,167,424 bytes (about 181/191 MiB of 700 MiB), with all cgroup OOM counters zero. Host available memory was about 951 MiB, swap unused, and root disk had about 32 GiB free. CPU was not sampled continuously during the waves.
- The application runs in Docker on the ECS itself, bound to 127.0.0.1:8012. Caddy on the same ECS terminates public TLS and routes locally to the container; there is no reverse SSH tunnel or dependency on the laptop. The current configured admission ceiling is 16 Add and 16 Search. This test validates one simultaneous batch at that ceiling, not sustained load or hardware capacity above the configured limit. The 16-way latency is high and Add approaches the configured per-provider request timeout, so concurrency was not raised.
- The submitted Authorization: Token credential was written to /opt/eviepoch-ecs/.env (mode 0600), then the container was recreated and became healthy with zero restarts. The prior local credential already matched the submitted value and had authenticated successfully before recreation. Public health returned 200; valid-token top_k=0 Search returned 200; invalid-token Add and Search returned 401.

Export .com audit records from Windows with:

    & 'D:\MyLab\EviEpoch\deploy\export-audit.ps1' -ServerHost '47.82.102.145' -KeyPath 'D:\下载\hongkong_server_key.pem' -Destination (Join-Path $env:USERPROFILE 'Downloads\EviEpoch-bbroot-audit')

Keep downloaded payload logs private and outside Git. No credentials are stored in this document.



## .de5.net instance route and audit follow-up (2026-10-29)

The EviEpoch systemd service and payload audit were already active on bubblevan.de5.net, but the active Caddy payment-site configuration had no EviEpoch paths and sent unknown routes to the payment runtime. This caused public EviEpoch paths to return 404. The site configuration now has exact Add, Search, and versioned health routes to loopback 127.0.0.1:8012. The change was validated and Caddy reloaded; the original payment health and callback routes were preserved. The prior site configuration is backed up at /var/backups/eviepoch-de5-20261029/stablepay-mvp.caddy.before.

After the route repair, external GET /v1/memories/health returned HTTP 200 with TLS verification; unauthenticated Add and Search returned HTTP 401. The existing payment /health still returned 200 and GET /api/payments/alipay/notify still returned 405. An authenticated zero-work Search through Caddy returned HTTP 200 and was recorded by the audit middleware.

The latest .de5 export contains 53 synthetic records across two JSONL files: Add 200: 18, Search 200: 25, Search 503: 10. The previously reported 16-way provider test remains the capacity evidence: Add 16/16 succeeded, while Search at top_k=100 returned 10/16 HTTP 200 and 6 HTTP 503. The official Smoke shown earlier was run before this audit capture was enabled; those historical request bodies were not recovered. Future authenticated Add/Search requests that reach this endpoint are recorded. The current export is under Downloads/EviEpoch-de5-audit-20261029.
