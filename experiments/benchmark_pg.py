"""Disposable PostgreSQL-only exact retrieval measurements. No provider/capacity extrapolation."""
import argparse
import json
import math
import os
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eviepoch.config import Settings
from eviepoch.domain import Source, encode, namespace, stable_id
from eviepoch.providers import Providers
from eviepoch.storage import Repository
from eviepoch.storage.migrate import TABLES, migrate


def percentile(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values)*p)-1)]


def seed(admin, count, dimensions, ns, foreign=False):
    rng = np.random.default_rng(1729 + count + int(foreign))
    source_rows, vectors, postings = [], [], []
    for i in range(count):
        oid = stable_id(ns, i)
        group = i % 97
        body = f"component{group} deployment event {i}"
        source = Source(oid, ns, f"r{i}", "public-perf", i, "user", body, None, "2026-10-27T00:00:00Z", 1)
        source_rows.append((ns, oid, "source", encode(source), body, None, 1, 1, "", "", "", None, None, 4))
        v = rng.normal(size=dimensions).astype(np.float32)
        v /= np.linalg.norm(v)
        vectors.append((ns, oid, "[" + ",".join(format(float(x), ".7g") for x in v) + "]"))
        postings.extend((ns, term, oid, 1) for term in (f"component{group}", "deployment", "event", str(i)))
        if len(source_rows) >= 512 or i == count-1:
            with psycopg.connect(admin) as conn:
                with conn.cursor().copy("COPY ee_objects FROM STDIN") as out:
                    for row in source_rows:
                        out.write_row(row)
                with conn.cursor().copy("COPY ee_vectors FROM STDIN") as out:
                    for row in vectors:
                        out.write_row(row)
                with conn.cursor().copy("COPY ee_postings FROM STDIN") as out:
                    for row in postings:
                        out.write_row(row)
            source_rows, vectors, postings = [], [], []


def measure(repo, ns, dimensions, concurrency, repeats):
    rng = np.random.default_rng(48)
    query = rng.normal(size=dimensions).astype(np.float32)
    query /= np.linalg.norm(query)
    def run(_):
        started = time.perf_counter()
        with repo.view(ns) as view:
            a = time.perf_counter()
            dense = view.dense(query, 100)
            b = time.perf_counter()
            lexical = view.lexical("component13 deployment", 100)
            c = time.perf_counter()
            snapshot = view.snapshot(set(dense) | set(lexical))
            end = time.perf_counter()
        assert all(s.namespace == ns for s in snapshot.sources.values())
        return {"connection_and_total_ms": (end-started)*1000, "dense_ms": (b-a)*1000,
                "bm25_ms": (c-b)*1000, "snapshot_ms": (end-c)*1000,
                "candidate_count": len(snapshot.sources)}
    run(0)  # Explicitly report warm DB cache; this is not a cold full-service test.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(run, range(repeats)))
    summary = {key: {"p50": percentile([r[key] for r in rows], .5),
                     "p95": percentile([r[key] for r in rows], .95),
                     "p99": percentile([r[key] for r in rows], .99)}
               for key in ("dense_ms", "bm25_ms", "snapshot_ms", "connection_and_total_ms")}
    # Capture actual database plans through the same query implementation, including namespace scope.
    with repo.view(ns) as view:
        executed = []
        original = view.c
        class RecordingConnection:
            def execute(self, statement, args=()):
                executed.append((statement, args))
                return original.execute(statement, args)
        view.c = RecordingConnection()
        view.dense(query, 100)
        view.lexical("component13 deployment", 100)
        plans = []
        for statement, params in executed:
            plan = original.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + statement, params).fetchone()[0]
            plans.append(plan)
    return {"concurrency": concurrency, "samples": repeats, "latency_ms": summary, "explain_analyze_buffers": plans}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[1000, 10000, 50000])
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 4])
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--reuse", action="store_true", help="measure an existing single scale point without reseeding")
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/p1-postgres-scale.json"))
    args = parser.parse_args()
    admin, service = os.environ["EPOCH_PERF_ADMIN_DATABASE_URL"], os.environ["EPOCH_PERF_DATABASE_URL"]
    if not urlsplit(admin).path.endswith("_test") or urlsplit(admin).path != urlsplit(service).path:
        parser.error("admin/service must address the same disposable database ending in _test")
    config = Settings(database_url=service, embed_dimensions=args.dimensions, embed_model="synthetic-perf-v1",
                      embed_base_url="https://public-perf.invalid/v1")
    migrate(admin, "eviepoch_app", config)
    with psycopg.connect(admin) as conn:
        environment = {"postgres": conn.execute("SELECT version()").fetchone()[0],
                       "pgvector": conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0],
                       "shared_buffers": conn.execute("SHOW shared_buffers").fetchone()[0],
                       "max_parallel_workers_per_gather": conn.execute("SHOW max_parallel_workers_per_gather").fetchone()[0]}
        if not args.reuse:
            conn.execute(sql.SQL("TRUNCATE {} ").format(sql.SQL(",").join(map(sql.Identifier, TABLES))))
    repo = Repository(config, Providers(config).embedding_identity())
    repo.open()
    ns, other = namespace("public-performance"), namespace("unrelated-public-performance")
    with repo.transaction(ns, True) as c:
        c.execute("INSERT INTO ee_namespaces VALUES(?,1,0) ON CONFLICT DO NOTHING", (ns,))
    report = {"official_capacity_verified": False, "scope": "PostgreSQL retrieval stages only; no LLM/HTTP/Add/Answer",
              "cache": "warm", "vectors": "deterministic synthetic normalized dense float32",
              "dimensions": args.dimensions, "candidate_limit": 100, "seed": 1729,
              "client": platform.platform(), "database": environment, "measurements": []}
    if not args.reuse:
        seed(admin, 1000, args.dimensions, other, True)
    for count in sorted(set(args.rows)):
        if count <= 0:
            parser.error("row counts must be positive")
        # Independent scale points avoid silently accumulating a different history size.
        seed_start = time.perf_counter()
        if args.reuse:
            with repo.transaction(ns) as c:
                actual = c.execute("SELECT COUNT(*) FROM ee_objects WHERE ns=?", (ns,)).fetchone()[0]
                if actual != count:
                    parser.error("existing scale does not match --rows")
        else:
            with psycopg.connect(admin) as c:
                c.execute("DELETE FROM ee_postings WHERE ns=%s", (ns,))
                c.execute("DELETE FROM ee_vectors WHERE ns=%s", (ns,))
                c.execute("DELETE FROM ee_objects WHERE ns=%s", (ns,))
            seed(admin, count, args.dimensions, ns)
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute("VACUUM ANALYZE ee_objects")
                c.execute("VACUUM ANALYZE ee_vectors")
                c.execute("VACUUM ANALYZE ee_postings")
        entry = {"user_sources": count, "user_vectors": count, "foreign_sources": 1000,
                 "seed_seconds": time.perf_counter()-seed_start, "runs": []}
        for concurrency in args.concurrency:
            entry["runs"].append(measure(repo, ns, args.dimensions, concurrency, args.samples))
        report["measurements"].append(entry)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"rows": count, "dimensions": args.dimensions,
                          "runs": [{"concurrency": r["concurrency"], "latency_ms": r["latency_ms"]} for r in entry["runs"]]}), flush=True)
    repo.close()


if __name__ == "__main__":
    main()
