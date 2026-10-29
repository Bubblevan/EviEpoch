"""Run synthetic unit/integration controls and save a secret-free validation record."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-postgres", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/offline-validation.json"))
    args = parser.parse_args()
    postgres = bool(os.getenv("EPOCH_TEST_DATABASE_URL") and os.getenv("EPOCH_TEST_ADMIN_DATABASE_URL"))
    if args.require_postgres and not postgres:
        parser.error("set disposable PostgreSQL test URLs before requiring integration validation")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ("eviepoch", "tests") for p in sorted((ROOT / folder).rglob("*.py"))}
    report = {"official_score": False, "scope": "independent synthetic contract/fault controls; no provider calls or private benchmark data",
              "commit_before_changes": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_hashes": hashes, "backends": ["sqlite", "postgresql"] if postgres else ["sqlite"],
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "seconds": round(time.perf_counter()-started, 3),
              "passed": result.wasSuccessful() and (not args.require_postgres or not result.skipped)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k != "source_hashes"}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
