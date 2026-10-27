"""Public long-input smoke with one deliberately capped real model completion."""
import argparse
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eviepoch.ingest.extraction import EXTRACTION_PROMPT
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from eviepoch.providers import Providers, StructuredOutputTruncated
from research_config import load


class CappedFirstExtraction(Providers):
    def __init__(self, config):
        super().__init__(config)
        self.injected = False
        self.real_truncations = 0

    def _structured(self, system, user, max_tokens=6000):
        if not self.injected and system.startswith(EXTRACTION_PROMPT):
            self.injected = True
            max_tokens = 48
        try:
            return super()._structured(system, user, max_tokens)
        except StructuredOutputTruncated:
            self.real_truncations += 1
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/p1-real-long-add.json"))
    args = parser.parse_args()
    config = replace(load(deepseek=args.deepseek), auth_scheme="none", workbench_enabled=True, database_url="")
    config.validate()
    tail = "安全约束：我对花生严重过敏，任何时候都不能吃花生。"
    long_text = "这是一段可丢弃的公开背景，没有新增个人事实。\n" * 2200 + tail
    messages = [Message("user", "In 2022 I lived in Kyoto. My favourite drink is tea."),
                Message("assistant", "記録しました。この発言は利用者の新しい事実ではありません。"),
                Message("user", long_text)]
    provider = CappedFirstExtraction(config)
    report = {"scope": "self-authored public long input; DeepSeek research, not official capacity",
              "source_characters": sum(len(m.content) for m in messages), "messages": len(messages),
              "fault": "first real extraction completion capped to 48 output tokens", "status": "starting"}
    with tempfile.TemporaryDirectory() as folder:
        memory = MemoryPipeline(replace(config, db_path=str(Path(folder)/"long.sqlite3")), provider)
        memory.start()
        try:
            start = time.perf_counter()
            memory.add(AddRequest("public-long", "public-long", "s", messages))
            report["add_seconds"] = time.perf_counter()-start
            state = memory.inspect("public-long")
            actual = sorted(state["sources"], key=lambda s: s["ordinal"])
            report["source_roundtrip"] = [(s["role"], s["content"]) for s in actual] == [(m.role, m.content) for m in messages]
            originals = {s["id"]: s["content"] for s in actual}
            report["span_integrity"] = all(originals[s["source_id"]].encode()[s["byte_start"]:s["byte_end"]].decode() == s["quote"]
                                             for s in state["spans"])
            report["rare_constraint_extracted"] = any("花生" in s["quote"] for s in state["spans"])
            start = time.perf_counter()
            result, trace = memory.search_with_trace(SearchRequest("我有什么必须遵守的饮食限制？", "public-long", 3))
            report["search_seconds"] = time.perf_counter()-start
            report["rare_constraint_recovered"] = any(tail in row["content"] for row in result["data"])
            report["search_trace"] = trace
            report["status"] = "passed" if all(report[k] for k in ("source_roundtrip", "span_integrity",
                "rare_constraint_extracted", "rare_constraint_recovered")) and provider.real_truncations > 0 else "failed"
        except Exception as exc:
            report["status"] = "error"
            report["error"] = type(exc).__name__
        finally:
            report["observed_real_truncations"] = provider.real_truncations
            report["usage"] = provider.usage
            report["committed_generation"] = memory.inspect("public-long")["generation"]
            memory.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"search_trace"}}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
