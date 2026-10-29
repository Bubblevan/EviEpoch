# Deployment record

Updated: 2026-10-29 (Asia/Shanghai)

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
- LLM: deepseek-flash. Embeddings: text-embedding-v4, 1024 dimensions. Profile: full; model policy: research.
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
- The audit export contained three synthetic records: one Add 200 and two Search 200. No official evaluation data was present.
- The existing database volume remained mounted and the database was about 59 MiB. After restart the container was healthy and using about 49 MiB of its 700 MiB limit. The host root disk had about 32 GiB free; no OOM kill was found in the checked kernel log window.
- These checks do not establish that the .com instance can sustain 16 concurrent provider-backed requests; no concurrency stress test was run on this instance.

Export .com audit records from Windows with:

    & 'D:\MyLab\EviEpoch\deploy\export-audit.ps1' -ServerHost '47.82.102.145' -KeyPath 'D:\下载\hongkong_server_key.pem' -Destination (Join-Path $env:USERPROFILE 'Downloads\EviEpoch-bbroot-audit')

Keep downloaded payload logs private and outside Git. No credentials are stored in this document.

