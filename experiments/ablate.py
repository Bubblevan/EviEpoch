"""Frozen-extraction component ablations and exact-candidate selector counterfactuals."""
import argparse
import copy
import itertools
import json
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eviepoch.domain import digest, namespace, stable_id
from eviepoch.ingest.extraction import EXTRACTION_PROMPT, PLAN_PROMPT
from eviepoch.manifest import build
from eviepoch.providers import Providers
from eviepoch.retrieval import select
from evaluate import ANSWER_PROMPT, JUDGE_PROMPT, evaluate, summarize
from frozen_provider import FrozenPublicProvider
from research_config import load


FACTORS = ("relations_enabled", "consolidation_enabled", "reflection_enabled", "proof_binding_enabled")
IMPROVEMENTS = ("state_projection_enabled", "coverage_enabled", "episode_navigation_enabled", "disclosure_enabled")


def variants(config, factorial=False, improvements=False):
    factors = IMPROVEMENTS if improvements else FACTORS
    labels = ("no-state-projection", "no-coverage", "no-episodes", "no-disclosure") if improvements else (
        "no-relations", "no-consolidation", "no-reflection", "no-binding")
    result = {"full": config}
    if factorial:
        for values in itertools.product((False, True), repeat=4):
            name = "r_c_f_b=" + "".join(str(int(v)) for v in values)
            result[name] = replace(config, **dict(zip(factors, values)))
    else:
        for label, field in zip(labels, factors):
            result[label] = replace(config, **{field: False})
    return result


def selector_controls(data, provider, answer):
    result = {}
    for selector in ("beam", "greedy", "topk"):
        rows = []
        for case in data["cases"]:
            ns = namespace("public-control:" + case["id"])
            for query in case["searches"]:
                plan, evidence, budget, config = copy.deepcopy(provider.selection_records[query["query"]])
                selected = select(plan, evidence, config, query["top_k"], budget, selector)
                actual = {e.source_id for e in selected}
                groups = [{stable_id(ns, label.rsplit(":", 1)[0], int(label.rsplit(":", 1)[1]), "source-v1")
                           for label in group} for group in query.get("evidence_alternatives", [])]
                response = [{"id": e.id, "content": e.content, "score": e.score} for e in selected]
                text = json.dumps(response, ensure_ascii=False)
                row = {"op": "search", "case": case["id"], "id": query["id"], "seconds": 0,
                       "complete_evidence": any(g <= actual for g in groups) if groups else None,
                       "evidence_recall": max((len(g & actual)/len(g) for g in groups), default=None),
                       "leakage": any(v in text for v in query.get("forbidden_text", [])) or any(
                           stable_id(ns, label.rsplit(":", 1)[0], int(label.rsplit(":", 1)[1]), "source-v1") in actual
                           for label in query.get("forbidden_sources", [])),
                       "retrieval_abstained": not selected if query.get("must_abstain") else None,
                       "answer_correct": None, "candidate_hash": digest([vars(e) for e in evidence.values()]),
                       "plan_hash": digest(asdict(plan)), "budget": budget}
                if answer:
                    generated = provider._structured(ANSWER_PROMPT, json.dumps({"query": query["query"],
                        "options": query.get("options"), "evidence": response}, ensure_ascii=False), 1200)
                    judged = provider._structured(JUDGE_PROMPT, json.dumps({"question": query["query"],
                        "reference": query["reference"], "answer": generated.get("answer", "")}, ensure_ascii=False), 300)
                    if type(judged.get("correct")) is not bool:
                        raise ValueError("judge must return a boolean")
                    row["answer_correct"] = judged["correct"]
                rows.append(row)
        result[selector] = {"summary": summarize(rows), "rows": rows}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("datasets") / "public_controls.json")
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--answer", action="store_true")
    parser.add_argument("--factorial", action="store_true")
    parser.add_argument("--improvements", action="store_true", help="ablate state projection, global coverage, episodes and disclosure")
    parser.add_argument("--frozen", type=Path, help="reuse a public frozen artifact; no new extraction or query plans")
    parser.add_argument("--output", type=Path, default=Path("experiments/reports/p1-ablations.json"))
    args = parser.parse_args()
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    config = replace(load(deepseek=args.deepseek), database_url="", auth_scheme="none", workbench_enabled=False,
                     profile="full", selector="beam", **dict.fromkeys(FACTORS + IMPROVEMENTS, True))
    config.validate()
    provider = FrozenPublicProvider(Providers(config))
    if args.frozen:
        frozen = json.loads(args.frozen.read_text(encoding="utf-8"))
        expected_models = {"llm": config.llm_model, "embedding": config.embed_model, "dimensions": config.embed_dimensions}
        if (frozen.get("artifact_hash") != digest(frozen.get("records")) or
                frozen.get("dataset_hash") != digest(data) or frozen.get("models") != expected_models or
                frozen.get("prompts_hash") != digest([EXTRACTION_PROMPT, PLAN_PROMPT])):
            parser.error("frozen artifact data/model/hash mismatch")
        provider.records, provider.sealed = frozen["records"], True
    report = {"official_score": False, "dataset_hash": digest(data), "arms": {}, "errors": [],
              "protocol": "canonical L1 extraction/temporal review/query-plan frozen; same model, queries and budgets; single-factor removal",
              "selector_protocol": "identical recorded candidates/plan/budget; recovery and disclosure excluded",
              "factors": list(IMPROVEMENTS if args.improvements else FACTORS),
              "budget_hash": digest([config.answer_input_tokens, config.answer_prompt_tokens, config.answer_item_tokens,
                                     config.token_safety, config.char_budget]),
              "query_hash": digest([[q["query"], q.get("options"), q["top_k"]] for c in data["cases"] for q in c["searches"]])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    canonical = []
    try:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            for case in data["cases"]:
                canonical.extend(evaluate(case, config, provider, Path(folder), args.answer))
        provider.sealed = True
        artifact = provider.artifact()
        artifact["dataset_hash"] = digest(data)
        artifact_path = args.output.with_suffix(".frozen.json")
        artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        report["frozen_artifact_hash"] = artifact["artifact_hash"]
        # Capture selector counterfactuals before later variants replace the provider's current snapshot.
        report["fixed_candidate_selectors"] = selector_controls(data, provider, args.answer)
        for name, variant in variants(config, args.factorial, args.improvements).items():
            before = provider.frozen_hits
            rows = canonical if name == "full" else []
            if name != "full":
                with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
                    for case in data["cases"]:
                        rows.extend(evaluate(case, variant, provider, Path(folder), args.answer))
            report["arms"][name] = {"summary": summarize(rows), "rows": rows,
                                     "factor_settings": {f: getattr(variant, f) for f in report["factors"]},
                                     "frozen_artifact_hash": artifact["artifact_hash"], "replay_hits": provider.frozen_hits-before}
            print(json.dumps({"arm": name, **summarize(rows)}), flush=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "reason": str(exc) if isinstance(exc, ValueError) else None})
    report["manifest"] = build(config)
    report["actual_usage"] = provider.usage
    report["complete"] = not report["errors"] and len(report["arms"]) == len(variants(config, args.factorial, args.improvements))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
