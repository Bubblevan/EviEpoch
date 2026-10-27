"""Public-only capture/replay of model extraction, independent of ablated graph state."""
import copy
from dataclasses import asdict, replace

from eviepoch.domain import InvalidExtraction, digest
from eviepoch.providers import StructuredOutputTruncated
from evaluate import PublicReplay


class FrozenPublicProvider(PublicReplay):
    def __init__(self, actual):
        super().__init__(actual)
        self.records = {}
        self.sealed = False
        self.selection_records = {}
        self.frozen_hits = 0

    def _frozen(self, stage, key, call):
        ident = stage + ":" + digest(key)
        if ident not in self.records:
            if self.sealed:
                raise InvalidExtraction("frozen model artifact is missing a requested input")
            try:
                self.records[ident] = {"output": call()}
            except StructuredOutputTruncated:
                self.records[ident] = {"error": "StructuredOutputTruncated"}
        else:
            self.frozen_hits += 1
        record = self.records[ident]
        if record.get("error"):
            raise StructuredOutputTruncated("replayed output truncation")
        return copy.deepcopy(record["output"])

    @staticmethod
    def _sources(sources):
        return [{"id": s.id, "content": s.content, "role": s.role, "time": s.asserted_at,
                 "window": [s.locator.get("extraction_char_start"), s.locator.get("extraction_char_end")]}
                for s in sources]

    def extract(self, sources, known):
        # Canonical extraction never sees summaries that disappear in another arm.
        context = replace(known, nodes={k: n for k, n in known.nodes.items() if n.kind != "summary"})
        return self._frozen("extract", self._sources(sources), lambda: self.actual.extract(sources, context))

    def repair_extraction(self, sources, known, error):
        context = replace(known, nodes={k: n for k, n in known.nodes.items() if n.kind != "summary"})
        return self._frozen("repair", self._sources(sources), lambda: self.actual.repair_extraction(sources, context, error))

    def verify_changes(self, proposed, sources, known):
        return self._frozen("temporal-review", [n.id for n in proposed],
                            lambda: self.actual.verify_changes(proposed, sources, known))

    def plan(self, query, options):
        return self._frozen("query-plan", [query, options], lambda: self.actual.plan(query, options))

    def audit_evidence(self, query, plan, sources):
        return self._memo("audit", [query, plan.intents, asdict(plan.as_of), self._sources(sources.values())],
                          lambda: self.actual.audit_evidence(query, plan, sources))

    def bind_obligations(self, query, plan, snapshot):
        data = asdict(snapshot)
        for source in data["sources"].values():
            source.pop("stored_at", None)
        return self._memo("bind", [query, asdict(plan), data],
                          lambda: self.actual.bind_obligations(query, plan, snapshot))

    def record_selection(self, query, plan, evidence, budget, config):
        self.selection_records[query] = copy.deepcopy((plan, evidence, budget, config))

    def artifact(self):
        return {"schema": 1, "scope": "self-authored-public-controls-only", "records": self.records,
                "artifact_hash": digest(self.records),
                "models": {"llm": self.actual.config.llm_model, "embedding": self.actual.config.embed_model,
                           "dimensions": self.actual.config.embed_dimensions}}
