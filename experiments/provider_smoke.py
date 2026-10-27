"""Small public-data provider check. No benchmark/private data, no secrets in output."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eviepoch.manifest import build
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from research_config import load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/provider-smoke.json"))
    args = parser.parse_args()
    config = load(deepseek=args.deepseek)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "starting", "official_score": False, "stages": [], "failures": []}
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
        config = replace(config, db_path=str(Path(folder) / "smoke.sqlite3"), database_url="",
                         auth_scheme="none", workbench_enabled=True)
        config.validate()
        memory = MemoryPipeline(config)
        memory.start()
        try:
            rows = [("old", "In January 2022 I lived in Kyoto.", 1642204800000),
                    ("new", "In June 2024 I moved to Osaka. My current home is Osaka.", 1718409600000),
                    ("plan", "I plan to move to Tokyo in 2028. This is only a future plan.", 1718496000000)]
            for rid, content, timestamp in rows:
                start = time.perf_counter()
                memory.add(AddRequest(rid, "public-provider-smoke", rid, [Message("user", content, timestamp)]))
                report["stages"].append({"op": "add", "id": rid, "seconds": time.perf_counter() - start})
            for query, required, forbidden in (("Where do I currently live?", "Osaka", "Tokyo"),
                                                ("Where did I live in January 2022?", "Kyoto", "Tokyo")):
                start = time.perf_counter()
                result, trace = memory.search_with_trace(SearchRequest(query, "public-provider-smoke", 3))
                text = json.dumps(result, ensure_ascii=False)
                passed = required in text and forbidden not in text
                report["stages"].append({"op": "search", "query": query, "seconds": time.perf_counter() - start,
                                         "passed": passed, "response": result, "trace": trace})
                if not passed:
                    report["failures"].append(query)
            report["memory"] = memory.inspect("public-provider-smoke")
            report["usage"] = memory.providers.usage
            report["status"] = "passed" if not report["failures"] else "failed"
        except Exception as exc:
            # Do not serialize provider exception bodies: those can contain request material.
            report["status"] = "error"
            report["failures"].append(type(exc).__name__)
            from eviepoch.domain import InvalidExtraction
            if isinstance(exc, InvalidExtraction):
                report["validation_error"] = str(exc)
            report["usage"] = memory.providers.usage
        finally:
            memory.close()
    report["manifest"] = build(config)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "model": config.llm_model, "report": str(args.output),
                      "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
