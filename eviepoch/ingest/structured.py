"""Optional deterministic navigation for explicit structured assertions and tool traces.

No derived prose is API evidence. Every object links to unchanged original bytes.
"""
import json
import re

from ..domain import InvalidExtraction, Node, TimeBounds, locate, stable_id


def structured_nodes(source, known):
    text = source.content
    document = re.match(r"\s*(?:\[[^\]\n]{1,128}\]\s*)*document:\s*(?:Document:\s*)?"
                        r"(?P<name>[^\n]{1,200}\.(?:pdf|docx|txt|md))\s*\n"
                        r"(?:Native clip page|Page):[^\n]{1,80}\n", text, re.I)
    if document and source.role in {"user", "tool"} and "```text" in text:
        # Explicit document envelopes are data, including any instructions inside.
        # Index the exact page as navigation instead of asking a model to paraphrase
        # list cells into personal facts. No inferred facts or governance are emitted.
        span = locate(source, text)
        node = Node(stable_id(source.id, "document-page-navigation-v1"), "episode",
                    "document:" + document.group("name"), "document_page", text[:12000],
                    [source.id], [span.id], scope=source.session_id, tier="scenario", state="navigation")
        return [node], [span]
    # Whole JSON records or an explicit Corpus envelope, never JSON embedded in a question.
    match = re.match(r"\s*(?:Corpus:\s*)?(?P<update>UPDATE:\s*replace the prior value[^\n]*\n)?(?P<json>\{.*\})\s*$",
                     text, re.S | re.I)
    if not match:
        # Tool trajectories may carry a descriptive envelope. They are procedure examples,
        # never instructions executed by the memory server.
        start = text.find("{")
        if start < 0 or "trajectory" not in text[:start + 1500]:
            return [], []
        try:
            data = json.loads(text[start:])
        except (ValueError, TypeError):
            return [], []
        trajectory = data.get("trajectory") if isinstance(data, dict) else None
        if not isinstance(trajectory, list) or not trajectory or len(trajectory) > 10000:
            return [], []
        trace = []
        for turn in trajectory:
            if not isinstance(turn, dict):
                continue
            if isinstance(turn.get("content"), str):
                trace.append(turn["content"])
            for call in turn.get("tool_calls", []) if isinstance(turn.get("tool_calls", []), list) else []:
                if isinstance(call, dict) and isinstance(call.get("function"), dict):
                    trace.extend(str(call["function"].get(key, "")) for key in ("name", "arguments"))
        if not trace:
            return [], []
        span = locate(source, text)
        feedback = data.get("eval_feedback", {})
        outcome = json.dumps(feedback, ensure_ascii=False)[:2000] if isinstance(feedback, dict) else "unknown"
        node = Node(stable_id(source.id, "procedure-navigation-v1"), "procedure", "trajectory",
                    "worked_example", ("Source-reported outcome: " + outcome + "\n" + "\n".join(trace))[:24000], [source.id], [span.id],
                    scope=str(data.get("dataset", "tools"))[:128], cardinality="multi", asserted_at=source.asserted_at)
        return [node], [span]
    try:
        data = json.loads(match.group("json"))
    except (ValueError, TypeError):
        return [], []
    fields = ("subject", "relation", "object", "subject_id", "relation_id", "object_id")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in fields):
        return [], []
    if source.role not in {"user", "tool"}:
        return [], []
    scope = data.get("scope", "general")
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 128:
        return [], []
    modality = data.get("modality", "asserted")
    if not isinstance(modality, str) or modality not in {"asserted", "planned", "hypothetical", "negated"}:
        return [], []
    try:
        valid_time = TimeBounds.parse(data.get("valid_date"))
        event_time = TimeBounds.parse(data.get("event_date"))
    except InvalidExtraction:
        return [], []
    span = locate(source, match.group("json"))
    node = Node(stable_id(source.id, "structured-assertion-v1"), "fact", data["subject"], data["relation"],
                data["object"], [source.id], [span.id], cardinality="single" if match.group("update") else "multi",
                asserted_at=source.asserted_at, scope=scope, modality=modality, valid_time=valid_time,
                occurrence_time=event_time, subject_id=data["subject_id"], predicate_id=data["relation_id"],
                value_id=data["object_id"])
    if (match.group("update") or data.get("operation") == "replace") and modality == "asserted":
        node.cardinality = "single"
        node.change = "correction"
        node.change_proposal = "explicit_replacement"
        node.change_verified = True
        node.change_targets = sorted(n.id for n in known.nodes.values() if n.kind not in {"summary", "episode"}
                                     and n.key == node.key and not (node.asserted_at and n.asserted_at
                                                                   and node.asserted_at < n.asserted_at))
    return [node], [span]
