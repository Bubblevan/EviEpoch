"""Deterministic temporal-label fault injection; not a model accuracy benchmark."""
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_lifecycle import AnnotatedProviders, fact
from eviepoch.config import Settings
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline


def main():
    rows = []
    languages = [
        ("In 2022 my address was Kyoto.", "In 2024 I moved to Osaka."),
        ("2022 年我的住址在京都。", "2024 年我搬到了大阪。"),
        ("2022年、私は京都に住んでいました。", "2024年に大阪へ引っ越しました。"),
    ]
    for language, (old, new) in enumerate(languages):
        for reviewed in ("correction", "retraction", "uncertain", "change"):
            provider = AnnotatedProviders()
            provider.register(old, [fact(old, "Kyoto", valid_date="2022", time_quote="2022")])
            # Force the bad extraction label, including when the reviewer repeats the same error.
            provider.register(new, [fact(new, "Osaka", change="correction", valid_date="2024", time_quote="2024")])
            provider.verify_changes = lambda proposed, sources, known, mode=reviewed: {"decisions": [
                {"id": n.id, "classification": mode, "target_ids": [p.id for p in known.nodes.values()
                 if p.kind == "fact"]} for n in proposed]}
            provider.plan_value = {"intents": ["as_of"], "as_of": "2022", "obligations": [{"query": "address 2022"}]}
            with tempfile.TemporaryDirectory() as folder:
                config = Settings(db_path=str(Path(folder)/"fault.sqlite3"), embed_dimensions=32, auth_scheme="none",
                                  llm_api_key="oracle", workbench_enabled=True)
                memory = MemoryPipeline(config, provider)
                memory.start()
                try:
                    memory.add(AddRequest("old", "u", "s", [Message("user", old)]))
                    state = memory.inspect("u")
                    old_source = state["sources"][0]["id"]
                    memory.add(AddRequest("new", "u", "s", [Message("user", new)]))
                    state = memory.inspect("u")
                    retrieved = memory.search(SearchRequest("address 2022", "u", 5))["data"]
                    rows.append({"language": language, "injected_review": reviewed,
                                 "false_invalidation": any(e["kind"] in {"CORRECTS", "RETRACTS"} for e in state["edges"]),
                                 "source_suppressed": old_source not in {s["id"] for s in state["sources"]},
                                 "historical_recovered": old_source in {r["id"] for r in retrieved}})
                finally:
                    memory.close()
    report = {"scope": "12 adversarial controls with oracle extraction/synthetic embeddings; not GPT/DeepSeek accuracy",
              "cases": len(rows), "false_invalidation_rate": sum(r["false_invalidation"] for r in rows)/len(rows),
              "false_suppression_rate": sum(r["source_suppressed"] for r in rows)/len(rows),
              "historical_recovery_rate": sum(r["historical_recovered"] for r in rows)/len(rows), "rows": rows}
    output = Path("experiments/reports/p1-temporal-faults.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
    return int(any(r["false_invalidation"] or r["source_suppressed"] or not r["historical_recovered"] for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
