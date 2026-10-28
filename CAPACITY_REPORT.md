# EviEpoch ECS deployment and capacity report

Date: 2026-10-28 (Asia/Shanghai)

## Deployed service

- Public origin: `https://bubblevan.de5.net`
- Add: `POST /v1/memories/add`
- Search: `POST /v1/memories/search`
- Health: `GET /v1/memories/health`
- Auth: `Authorization: Bearer <Memory System Key>`
- Runtime source tree: commit `55d14b147408638c3b61265c72a0a309f53115b8`
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

This deployment probe called real paid providers once through one Add and one Search; the API does not expose provider token usage, so this report does not claim an exact monetary cost. No concurrent provider-backed stress test was run.

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
