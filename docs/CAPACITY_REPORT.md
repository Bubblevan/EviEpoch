# EviEpoch ECS deployment and capacity report

Date: 2026-10-28 (Asia/Shanghai)

## Deployed service

- Public origin: `https://bubblevan.de5.net`
- Add: `POST /v1/memories/add`
- Search: `POST /v1/memories/search`
- Health: `GET /v1/memories/health`
- Auth: `Authorization: Bearer <Memory System Key>`
- Runtime source tree: commit `1bfd011872374ec98b7885c9b1cbe23801f68a5f`
- Persistent SQLite database: `/var/lib/eviepoch/eviepoch-v1.sqlite3`
- Payload audit: `/var/lib/eviepoch/audit`
- The application runs as `eviepoch` under systemd; Caddy terminates public HTTPS and proxies the path prefix to loopback port 8012. The app port is not publicly bound.

## Configured capacity and resource limits

- ECS: 2 vCPU, 2 GiB RAM, 40 GiB root disk; after verification, about 1.1 GiB RAM and 35 GiB disk were available.
- One Uvicorn worker; application limits are 16 concurrent Add and 16 concurrent Search.
- Uvicorn accepts up to 36 concurrent requests total.
- systemd limits the app cgroup to 150% CPU, 550 MiB `MemoryHigh`, and 700 MiB `MemoryMax`.
- Audit capture stores at most 1 MiB of each request body and response body. The ECS audit directory is capped at 8 GiB, with 30-day retention. The JSONL contains authenticated Add/Search bodies and responses, status and duration; it excludes authorization headers and does not capture requests with invalid credentials.

These are configured service limits. The public provider-backed concurrency ceiling and throughput have not been measured in a multi-request load run.

## Verification evidence

### Automated tests

Local command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

Result: 105 total; 60 passed and 45 PostgreSQL integration tests skipped because no test database was configured; 6.276 s. No paid provider calls were part of this suite.

ECS Linux command (temporary test DB and non-production settings):

```sh
tmpdb=$(mktemp /tmp/eviepoch-tests-XXXXXX.sqlite3)
EPOCH_PROFILE=b0 EPOCH_AUTH_SCHEME=none EPOCH_DB_PATH="$tmpdb" /opt/eviepoch-memory/venv/bin/python -m unittest discover -s tests
rc=$?
rm -f "$tmpdb"*
exit $rc
```

Result: 105 total; 60 passed and 45 skipped; 3.240 s. The deployed app code was unchanged after this Linux run; later commits only changed operator documentation and license notices.

### Public API probe

- Public TLS `GET /v1/memories/health`: HTTP 200, certificate verification succeeded.
- Valid-schema Add and Search requests without credentials: HTTP 401 each.
- One synthetic, authenticated Add using the configured DeepSeek and text-embedding-v4 providers: HTTP 200.
- The service was restarted after Add. One authenticated Search then returned HTTP 200 and one evidence item containing the synthetic fact. This verifies the public route, provider-backed Add/Search and SQLite persistence across a service restart.
- Audit durations for this one-sample check: Add 5,711.66 ms; Search 3,555.04 ms. Each is one sample and is not a capacity percentile.
- The resulting JSONL had two records (Add and Search), 1,173 bytes, directory mode `0700`, file mode `0600`; the Memory System Key was absent. SCP export downloaded one file with two records and left the server copy intact. The temporary export check was removed.
- After the live probe and before the final restart, the service cgroup reported 150,913,024 bytes and the Uvicorn process RSS was 177,752 KiB. After restart, cgroup memory was 49,098,752 bytes. Root disk remained about 35 GiB free. No OOM occurred during this single-sample verification.

This deployment probe called real paid providers once through one Add and one Search; the API does not expose provider token usage, so this report does not claim an exact monetary cost. The subsequent provider-backed concurrency test is recorded below.

## Repeatable operator checks

```sh
systemctl is-active eviepoch-memory caddy
curl -fsS https://bubblevan.de5.net/v1/memories/health
```

From Windows, export current payload logs with:

```powershell
& 'D:\MyLab\EviEpoch\deploy\export-audit.ps1'
```

The script defaults to the verified ECS IP and strict SSH host-key checking. It stores downloads under `Downloads\EviEpoch-audit` and leaves server originals intact. Keep exported request bodies private and out of GitHub.

The earlier local fake-provider capacity run is preserved in [CAPACITY_REPORT_LOCAL_2026-10-26.md](CAPACITY_REPORT_LOCAL_2026-10-26.md); its Docker Desktop measurements are not ECS measurements.

## Provider-backed 16-way concurrency test (2026-10-28)

This was a bounded synthetic test through the public HTTPS routes, not an official Smoke or Full evaluation. It used one isolated synthetic user, unique request IDs, 16 concurrent workers, no outer HTTP retries, and small synthetic messages. Search was tested once at top_k=5, then again at the evaluation setting top_k=100; the second wave did not add more memories.

| Operation | Concurrency | Result | Latency |
|---|---:|---:|---|
| Add | 16 | 16/16 HTTP 200 | p50 2,486 ms; p95/p99 2,834 ms |
| Search, top_k=5 | 16 | 12/16 HTTP 200; 4/16 HTTP 503 | p50 13,984 ms; p95/p99 28,899 ms |
| Search, top_k=100 | 16 | 10/16 HTTP 200; 6/16 HTTP 503 | p50 13,334 ms; p95/p99 26,222 ms |

All 12 successful searches in the first wave and all 10 successful searches in the top_k=100 wave returned their matching synthetic marker. These are single batches of 16; nearest-rank p95/p99 therefore equal the slow tail and are not stable service-level estimates.

The audit export contained 50 authenticated Add/Search records: 17 Add 200 (one earlier deployment probe plus 16 test Adds), 23 Search 200, and 10 Search 503. The four first-wave and six second-wave Search failures all had the same generic response: required model stage unavailable or source validation failed. The application maps ProviderUnavailable and InvalidExtraction to this 503 response, so the available public response does not distinguish provider failure from source validation. No 429 was returned. The API's structured error diagnostics did not identify a more specific cause in the captured logs.

During the test, the service cgroup peak was 163,930,112 bytes (about 156 MiB), below its 700 MiB cap; the host root disk had about 35 GiB free. No OOM was observed. The test establishes that one 16-way Add burst succeeded, but Search did not meet a 16-way success target. The current service is not validated for 16 concurrent Search and should not be described as 16-way evaluation-ready. The 503s need stage-specific, content-free diagnostics before attributing them conclusively to the ECS, SQLite, or a provider.

The downloaded audit file is for private analysis only; keep it outside Git. This provider-backed test used DeepSeek and text-embedding-v4. Provider token usage and monetary cost are not exposed by the current service, so no exact cost is claimed. No official Smoke or Full was started.


## Secondary .com ECS deployment (2026-10-29)

The restored Hong Kong ECS at bubblevan.bbroot.com is now running the same EviEpoch application code on an independent configuration and database, separately from bubblevan.de5.net. The two instances do not share SQLite data.

The existing Compose project and named SQLite volume were preserved. The application listens on 127.0.0.1:8012 behind the existing trusted Caddy certificate. Public /health and /v1/memories/health both returned HTTP 200 with TLS verification; unauthenticated Add and Search returned HTTP 401. A valid authenticated zero-work Search (top_k=0) through the Caddy domain route returned HTTP 200.

One synthetic provider-backed Add and Search were run through the container application endpoint: Add HTTP 200 in 3,141 ms; Search HTTP 200 in 1,680 ms and returned the matching marker. This is a single functional probe, not a concurrency measurement. The exported .com audit file contained 3 records (Add 200: 1, Search 200: 2), all synthetic; request bodies were present and are stored locally at Downloads/EviEpoch-bbroot-audit-20261029. No official Smoke or Full was started.

The .com service is configured for 16 concurrent Add and 16 concurrent Search requests, 1.5 CPU cores, 700 MiB RAM, 1 MiB per captured body, 8 GiB audit storage, and 30-day retention. The public provider-backed 8-way and 16-way results are recorded below.

Before the update, an online SQLite backup was stored at /var/backups/eviepoch-bbroot-20261029/eviepoch-v1-pre-audit.sqlite3 and the previous image was tagged eviepoch-ecs/eviepoch:pre-audit-20261029. The existing .env was not replaced. The deployed image includes the AMI MIT license notice.


## 2026-10-29 public route and light-load follow-up

The .de5.net service remained active, but its Caddy site config had been replaced by a payment-focused config with no EviEpoch routes. Exact Add/Search/health proxy routes have now been restored and Caddy validated/reloaded without changing payment routes. Public versioned health is 200/TLS-valid; unauthenticated Add/Search are 401. An authenticated top_k=0 Search through Caddy returned 200 and was audited. Historical official Smoke bodies from before audit enablement are not available; the latest .de5 logs contain 53 synthetic records, as listed in DEPLOYMENT.md.

A light 4-way test on the .com instance used synthetic data and top_k=100 Search through its Caddy TLS listener: Add 4/4 HTTP 200 (p50 3.53 s; p95/p99 4.97 s); Search 4/4 HTTP 200 (p50 5.29 s; p95/p99 9.84 s); all 4 searches matched their markers. This ran through the server's loopback Caddy listener with domain SNI, not across the external WAN. The latest .com audit export after the additional public tests and Token recheck contains 63 synthetic records: Add 200: 31 and Search 200: 32. These include the prior 4-way sample, authenticated zero-work checks, and the public 8-way and 16-way waves below.


## 2026-10-29 public provider-backed concurrency test

This test was sent from the Windows workstation through https://bubblevan.bbroot.com using the valid instance token and TLS verification. Each wave used a unique synthetic user, one seeded synthetic evidence item, Add messages with unique request IDs, Search top_k=100, and no client retries. Add and Search began simultaneously. The timing includes the WAN/TLS path and provider latency.

| Concurrent Add | Concurrent Search | Total client requests | Add result and latency | Search result and latency | Wave time |
|---:|---:|---:|---|---|---:|
| 8 | 8 | 16 | 8/8 HTTP 200; p50 19,469.90 ms; p95/p99 21,618.74 ms | 8/8 HTTP 200; 8/8 seeded evidence matches; p50 9,983.21 ms; p95/p99 18,142.04 ms | 21.65 s |
| 16 | 16 | 32 | 16/16 HTTP 200; p50 35,474.54 ms; p95/p99 35,968.56 ms | 16/16 HTTP 200; 16/16 seeded evidence matches; p50 16,993.01 ms; p95/p99 33,220.44 ms | 35.98 s |

Both batches reached the configured per-route ceiling of 16 Add and 16 Search and returned no HTTP errors. These are single batches, not a sustained soak test or evidence for concurrency above the configured limit. At 16, Add latency approaches the configured 40-second provider timeout; the service was not reconfigured above 16.

After the 16-way batch, the container was healthy with zero restarts. Cgroup memory current was 189,435,904 bytes and peak 200,167,424 bytes (about 181 MiB and 191 MiB, respectively, under a 700 MiB limit); memory.events reported zero high, max, oom, and oom_kill events. Host available memory was about 951 MiB, swap was unused, and root disk had about 32 GiB free. CPU was not continuously sampled during the requests, so no CPU peak is claimed.

The latest SCP export is under Downloads/EviEpoch-bbroot-audit-20261029-capacity: 63 JSONL records, Add 200: 31 and Search 200: 32. After the env re-application and container recreation, public Health returned 200, valid-token top_k=0 Search returned 200, and invalid-token Add/Search both returned 401. The prior local credential already matched the submitted value and had worked before recreation. The records correspond to synthetic probes, load tests, and this zero-work auth check; this turn did not run official Smoke or Full. The application and SQLite volume run on the ECS in Docker; Caddy on that same ECS provides public HTTPS and forwards only to loopback 127.0.0.1:8012. No reverse SSH tunnel to the laptop is involved.
