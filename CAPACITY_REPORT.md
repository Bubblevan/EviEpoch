# EviEpoch capacity check

Date: 2026-10-26 (Asia/Shanghai)

## What limits concurrency

- AML's current Full job form exposes `Max Add concurrency` and `Search concurrency`. The official rules page lists Add 16–64 and Search 16–256; the selected values are upper bounds, not guaranteed request rates. AML says actual concurrency depends on worker capacity and service load.
- There is no Alibaba Cloud setting called “API concurrency” for this deployment. The ECS instance is a Caddy/SSH relay. Its CPU, RAM, public bandwidth, and socket/network limits can constrain forwarding, but the memory API, SQLite, and model calls run on the Docker Desktop host.
- The running EviEpoch image starts one Uvicorn worker and uses one persistent SQLite connection protected by a process-wide re-entrant lock. Search reads and scores every stored vector for the selected user, so cost grows with that user's memory count. Provider latency and provider rate limits are additional constraints.

## Observed environment

- Running container: `eviepoch-eviepoch-1`, Docker health `healthy`, mapped only to `127.0.0.1:8012`.
- Image command: `uvicorn eviepoch.api:app --host 0.0.0.0 --port 8000 --workers 1`.
- Docker Desktop reports 32 CPUs and 31.22 GiB available to its VM. At idle, `docker stats` showed 0.12% CPU and 94.79 MiB container memory.
- The last confirmed ECS snapshot from deployment was 2 vCPU, about 407 MiB RAM, and 20 GiB disk. That machine forwards HTTPS/SSH traffic; it is not the current SQLite/search compute host.

## Synthetic API load test

The test runs the EviEpoch API image with one Uvicorn worker, 1024-dimensional deterministic fake embeddings, a fake fact extractor/query rewriter, and an isolated temporary SQLite database. It does not read `.env`, use production data, or contact Vercel, DashScope, or any other model endpoint.

Command:

```powershell
docker run --rm --mount "type=bind,source=D:\MyLab\EviEpoch\experiments\capacity_synthetic.py,target=/capacity_synthetic.py,readonly" eviepoch-eviepoch python /capacity_synthetic.py
```

Run duration: 28.22 s. Total: 200 Add, 280 Search (200 on the small profile plus 80 on the scale profile). All responses were HTTP 200; no 429 or 503 responses occurred. Final SQLite file: 48,570,368 bytes. Test process peak RSS: 1,722,436 KiB (about 1.64 GiB).

### Small corpus: 400 memories, 1024 dimensions

Each concurrency level ran 40 Add requests and then 40 Search requests. Latencies are per-request milliseconds; throughput is requests per second.

| Concurrency | Add success | Add p50 / p95 / p99 ms | Add req/s | Search success | Search p50 / p95 / p99 ms | Search req/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 40/40 | 0.92 / 1.29 / 19.15 | 685.19 | 40/40 | 3.44 / 5.07 / 344.91 | 81.62 |
| 4 | 40/40 | 14.11 / 19.15 / 21.42 | 301.95 | 40/40 | 28.65 / 73.43 / 94.76 | 108.06 |
| 8 | 40/40 | 19.15 / 38.47 / 43.40 | 295.05 | 40/40 | 109.49 / 269.04 / 369.00 | 57.01 |
| 16 | 40/40 | 14.91 / 24.16 / 32.03 | 442.40 | 40/40 | 338.68 / 551.21 / 562.50 | 40.83 |
| 32 | 40/40 | 29.96 / 47.40 / 50.33 | 419.80 | 40/40 | 543.60 / 708.09 / 758.43 | 44.02 |

### Search corpus scale: 10,000 memories, 1024 dimensions

Twenty Search requests were run at each level for the same user, returning at most `top_k=100` evidence items.

The 10,000-row search corpus was seeded through the same SQLite store transaction method rather than issuing 10,000 HTTP Add requests; the separate HTTP Add test above covers the Add endpoint and persistence path.

| Concurrency | Success | p50 ms | p95 ms | p99 ms | Throughput req/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 165.12 | 190.70 | 223.10 | 5.92 |
| 4 | 20/20 | 1,174.18 | 1,656.11 | 1,766.84 | 2.95 |
| 8 | 20/20 | 2,379.60 | 3,248.53 | 3,271.07 | 3.08 |
| 16 | 20/20 | 3,346.53 | 5,668.40 | 5,711.67 | 2.95 |

The scale run shows the current full-scan Search path becoming the dominant limit as memory count and concurrent searches increase. This is a measured application/SQLite baseline, not a provider-backed or public-internet capacity result.

## Public path and provider checks

- The public monitor's last recorded check was PASS at `2026-10-26T18:01:37.681+08:00`, with a 4.61 s public empty-Search/authentication round trip.
- During this capacity session, shell HTTPS to the public endpoint timed out during TLS handshake, and read-only SSH to ECS timed out during banner exchange. The browser navigation also timed out. These failures came from the current isolated test environment; they do not establish that the public service is down. A fresh public-path capacity result is therefore unavailable from this session.
- No real model provider was called in this load test. The latency and 429/503 profile of `gpt-4o-mini` plus `text-embedding-v4` is still governed by provider quota and needs a separate cost-approved probe.

## Recommended declared settings

Use the AML minimum settings, Add 16 and Search 16, as the initial evaluation target. The local synthetic API passed those concurrency levels, including a 10,000-memory Search corpus; use the provider-backed and public-path checks to confirm operational readiness before starting a formal Full run. Do not infer 300M-token capacity from this corpus-size test: token totals, Add chunk count, model request rate, and unique-user distribution are not interchangeable.
