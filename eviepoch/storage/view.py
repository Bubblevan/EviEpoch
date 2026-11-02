"""Read views and transaction adapters, separate from repository publication."""
from __future__ import annotations

import json
import math
import time

import numpy as np

from ..domain import Edge, Node, Snapshot, Source, Span, node_from_dict
from ..retrieval.lexical import terms
from .entities import entity_key as _entity_key

class ReadSession:
    """Optimistic read: the caller must validate both generations before publishing."""
    def __init__(self, repo, ns):
        self.repo, self.ns = repo, ns
        self.timings_ms = {"database_open": 0.0, "database_work": 0.0}
        started = time.perf_counter()
        with repo.view(ns) as view:
            self.timings_ms["database_open"] += (time.perf_counter() - started) * 1000
            self.generation, self.policy_generation = view.generation, view.policy_generation

    def __getattr__(self, name):
        if name not in {"dense", "lexical", "temporal", "snapshot", "related", "graph_candidates",
                        "coverage", "follow", "source_neighbors", "coding_candidates"}:
            raise AttributeError(name)
        def read(*args, **kwargs):
            started = time.perf_counter()
            with self.repo.view(self.ns) as view:
                acquired = time.perf_counter()
                self.timings_ms["database_open"] += (acquired - started) * 1000
                result = getattr(view, name)(*args, **kwargs)
                self.timings_ms["database_work"] += (time.perf_counter() - acquired) * 1000
                return result
        return read

class Connection:
    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def execute(self, sql, args=()):
        if self.pg:
            return self.raw.execute(sql.replace("?", "%s"), args if args else None)
        return self.raw.execute(sql, args)

class View:
    def __init__(self, repo, c: Connection, ns: str):
        self.repo, self.c, self.ns = repo, c, ns
        row = c.execute("SELECT generation,policy_generation FROM ee_namespaces WHERE ns=?", (ns,)).fetchone()
        self.generation, self.policy_generation = row or (0, 0)

    def related(self, nodes: list[Node]) -> Snapshot:
        ids = set()
        for key in {n.key for n in nodes}:
            ids.update(r[0] for r in self.c.execute(
                "SELECT id FROM ee_objects WHERE ns=? AND active>0 AND subject_key=? AND predicate_key=? "
                "AND scope_key=? ORDER BY generation DESC LIMIT 256", (self.ns, *key)).fetchall())
        return self.snapshot(ids)

    def graph_candidates(self, entities: list[str], attributes: list[str] | None,
                         limit: int, max_hops: int, query: str = "") -> dict[str, float]:
        """Retrieve exact-entity and explicit relation paths independently of dense/BM25 seeds."""
        budget = min(max(0, limit), self.repo.config.graph_budget)
        hops = min(max(0, max_hops), 4)
        entity_keys = list(dict.fromkeys(_entity_key(value) for value in entities if _entity_key(value)))[:32]
        attribute_keys = list(dict.fromkeys(_entity_key(value) for value in (attributes or [])
                                            if _entity_key(value)))[:32]
        query_terms = set(terms(query))
        if not budget or not entity_keys and not attribute_keys:
            return {}
        layer_budget = max(1, budget // max(1, hops + 1))

        def lookup(keys, roles, *, ids=(), unbound_only=False, cap=layer_budget):
            keys = list(dict.fromkeys(keys))[:400]
            ids = list(dict.fromkeys(ids))[:400]
            if not keys and not ids:
                return {}
            terms = []
            if ids:
                terms.append("m.entity_id IN (" + ",".join("?" for _ in ids) + ")")
            if keys:
                label_clause = "m.entity_key IN (" + ",".join("?" for _ in keys) + ")"
                if unbound_only:
                    label_clause = "m.entity_id IS NULL AND " + label_clause
                terms.append(label_clause)
            statement = ("SELECT DISTINCT o.id,o.payload,o.generation FROM ee_entity_mentions m "
                         "JOIN ee_objects o ON o.ns=m.ns AND o.id=m.object_id "
                         "WHERE m.ns=? AND o.active>0 AND o.kind NOT IN ('source','span','summary','episode') "
                         "AND m.role IN (" + ",".join("?" for _ in roles) + ") AND (" + " OR ".join(terms) + ") "
                         "ORDER BY o.generation DESC,o.id LIMIT ?")
            # Bind parameters follow statement order: namespace, roles, then entity clauses.
            role_values = list(roles)
            entity_values = []
            if ids:
                entity_values.extend(ids)
            if keys:
                entity_values.extend(keys)
            return self.c.execute(statement, (self.ns, *role_values, *entity_values, cap)).fetchall()

        seeds = []
        if entity_keys:
            seeds.extend(lookup(entity_keys, ("subject", "object"), cap=budget))
        if attribute_keys:
            seeds.extend(lookup(attribute_keys, ("predicate",), cap=budget))
        layers, seen, payloads = [], set(), {}
        first = []
        for oid, raw, _generation in seeds:
            if oid not in seen:
                first.append(oid)
                seen.add(oid)
                payloads[oid] = json.loads(raw)
        if query_terms:
            def query_relevance(oid):
                data = payloads[oid]
                text = " ".join(str(data.get(field, "")) for field in
                                ("kind", "subject", "predicate", "value", "scope"))
                return len(query_terms & set(terms(text)))
            first.sort(key=lambda oid: (-query_relevance(oid), oid))
        first = first[:budget]
        seen = set(first)
        if first:
            layers.append(first)
        frontier_nodes = first[:layer_budget]
        for _depth in range(hops):
            if not frontier_nodes or len(seen) >= budget:
                break
            identity_ids, label_keys = set(), set()
            for oid in frontier_nodes:
                data = payloads.get(oid, {})
                for label_field, id_field in (("subject", "subject_id"), ("value", "value_id")):
                    entity_id = _entity_key(data.get(id_field))
                    entity_key = _entity_key(data.get(label_field))
                    if entity_id:
                        identity_ids.add(entity_id)
                    elif entity_key and entity_key not in entity_keys:
                        # The query anchor already expanded as a seed. Avoid spending
                        # each hop re-reading its high-degree, same-name neighborhood.
                        label_keys.add(entity_key)
            rows = lookup(label_keys, ("subject", "object"), ids=identity_ids,
                          unbound_only=True, cap=layer_budget)
            next_layer = []
            for oid, raw, _generation in rows:
                if oid not in seen:
                    next_layer.append(oid)
                    seen.add(oid)
                    payloads[oid] = json.loads(raw)
            edge_rows = self._edges_for(frontier_nodes, layer_budget)
            edge_ids = {oid for edge in edge_rows for oid in (edge.origin, edge.target)} - seen
            for kind, raw in self._objects(edge_ids):
                data = json.loads(raw)
                if data.get("id") not in seen and kind not in {"source", "span"}:
                    oid = data["id"]
                    next_layer.append(oid)
                    seen.add(oid)
                    payloads[oid] = data
            if next_layer:
                layers.append(next_layer[:max(0, budget - sum(map(len, layers)))])
            frontier_nodes = next_layer[:layer_budget]
        ordered, emitted = [], set()
        for index in range(max((len(layer) for layer in layers), default=0)):
            for layer in layers:
                if index < len(layer) and layer[index] not in emitted:
                    ordered.append(layer[index])
                    emitted.add(layer[index])
                    if len(ordered) >= budget:
                        break
            if len(ordered) >= budget:
                break
        return {oid: 1 / (rank + 1) for rank, oid in enumerate(ordered)}

    def context(self, text: str, limit=60) -> Snapshot:
        ranked = self.lexical(text, limit)
        recent = [r[0] for r in self.c.execute(
            "SELECT id FROM ee_objects WHERE ns=? AND active>0 AND kind NOT IN ('source','span','summary','episode') "
            "ORDER BY generation DESC,id LIMIT ?", (self.ns, limit)).fetchall()]
        return self.snapshot(set(ranked) | set(recent))

    def follow(self, nodes: list[Node]) -> Snapshot:
        """Follow explicit identity links, never join equal surface names speculatively."""
        subjects = {n.value_id.casefold() for n in nodes if n.value_id}
        rows = self._by_ids("SELECT id FROM ee_objects WHERE ns=? AND active>0 AND subject_key IN ({ids}) "
                            "ORDER BY generation DESC,id LIMIT " + str(self.repo.config.graph_budget), subjects)
        return self.snapshot({r[0] for r in rows[:self.repo.config.graph_budget]})

    def coverage(self, query: str, limit: int) -> list[str]:
        """Diversify lexical source candidates across sessions before within-session rank."""
        candidates = self.lexical(query, min(self.repo.config.graph_budget, limit * 8))
        broad = not candidates
        if broad:
            # A genuine "summarize our history" request may name no stored topic.
            # Seed both ends of the history; semantic verification happens outside SQL.
            cap = max(1, self.repo.config.graph_budget // 2)
            candidates = {}
            for order in ("ASC", "DESC"):
                rows = self.c.execute("SELECT id FROM ee_objects WHERE ns=? AND active>0 AND kind='source' "
                                      f"ORDER BY generation {order},id LIMIT ?", (self.ns, cap)).fetchall()
                candidates.update({r[0]: 1.0 for r in rows})
        snap = self.snapshot(set(candidates))
        groups = {}
        for source in snap.sources.values():
            if not broad and not set(terms(query)).intersection(terms(source.content)):
                continue
            groups.setdefault(source.session_id, []).append(source)
        for group in groups.values():
            group.sort(key=lambda s: (s.generation, s.ordinal, s.id))
        result = []
        for index in range(max(map(len, groups.values()), default=0)):
            for key in sorted(groups):
                if index < len(groups[key]):
                    result.append(groups[key][index].id)
                    if len(result) >= limit:
                        return result
        return result

    def source_neighbors(self, sources, radius: int, limit: int) -> Snapshot:
        """Bounded original-turn context within the same Add and session, still active.

        Generation scopes the indexed SQL scan to a single published Add. Never
        cross requests or infer adjacency from a similar speaker/entity name.
        """
        ids = set()
        ordinal = "CAST(payload::jsonb->>'ordinal' AS INTEGER)" if self.repo.pg else "CAST(json_extract(payload,'$.ordinal') AS INTEGER)"
        for source in sources:
            if radius <= 0 or len(ids) >= limit:
                break
            rows = self.c.execute(
                "SELECT id,payload FROM ee_objects WHERE ns=? AND active>0 AND kind='source' "
                f"AND generation=? AND {ordinal} BETWEEN ? AND ? ORDER BY {ordinal},id LIMIT ?",
                (self.ns, source.generation, max(0, source.ordinal-radius), source.ordinal+radius,
                 min(2*radius+1, limit-len(ids)))).fetchall()
            for oid, payload in rows:
                other = json.loads(payload)
                if other['request_id'] == source.request_id and other['session_id'] == source.session_id:
                    ids.add(oid)
        context = self.snapshot(ids)
        # Derived episode/summary dependencies may drill into additional sources.
        # They must not turn this bounded turn expansion into a whole-history scan.
        sources = {sid: source for sid, source in context.sources.items() if sid in ids}
        nodes = {nid: node for nid, node in context.nodes.items() if set(node.source_ids) <= sources.keys()}
        spans = {sid: span for sid, span in context.spans.items() if span.source_id in sources}
        edges = [edge for edge in context.edges if edge.origin in nodes and edge.target in nodes
                 and set(edge.source_ids) <= sources.keys()]
        return Snapshot(context.generation, context.policy_generation, sources, nodes, edges, spans)

    def coding_candidates(self, query: str, limit: int) -> dict[str, float]:
        from ..adapters.coding.search import coding_lexical_channel
        return coding_lexical_channel(self, query, limit)

    def lexical(self, query: str, limit: int) -> dict[str, float]:
        query_terms = list(dict.fromkeys(terms(query)))[:128]
        if not query_terms:
            return {}
        if self.repo.pg:
            # Aggregate postings and IDF in the database; transfer only the bounded candidate pool.
            rows = self.c.execute("""
                WITH stats AS (
                  SELECT COUNT(*)::double precision AS n,AVG(length)::double precision AS avg
                  FROM ee_objects WHERE ns=? AND active>0 AND length>0
                ), df AS (
                  SELECT p.term,COUNT(*)::double precision AS n FROM ee_postings p JOIN ee_objects o
                    ON o.ns=p.ns AND o.id=p.object_id
                  WHERE p.ns=? AND p.term=ANY(?::text[]) AND o.active>0 GROUP BY p.term
                )
                , scored AS (
                  SELECT p.object_id,SUM(LN(1+(s.n-d.n+0.5)/(d.n+0.5))*p.tf*2.2 /
                  (p.tf+1.2*(0.25+0.75*o.length/s.avg))) AS score
                  FROM ee_postings p JOIN ee_objects o ON o.ns=p.ns AND o.id=p.object_id
                  JOIN df d ON d.term=p.term CROSS JOIN stats s
                  WHERE p.ns=? AND p.term=ANY(?::text[]) AND o.active>0
                  GROUP BY p.object_id
                ), ranked AS (
                  SELECT s.object_id,s.score,ROW_NUMBER() OVER (PARTITION BY
                    CASE WHEN o.kind='span' THEN o.payload::jsonb->>'source_id' ELSE o.id END
                    ORDER BY s.score DESC,s.object_id) AS source_rank
                  FROM scored s JOIN ee_objects o ON o.ns=? AND o.id=s.object_id AND o.active>0
                )
                SELECT object_id,score FROM ranked WHERE source_rank<=3
                ORDER BY score DESC,object_id LIMIT ?
                """, (self.ns, self.ns, query_terms, self.ns, query_terms, self.ns, limit)).fetchall()
            return self._cap_source_candidates({oid: float(score) for oid, score in rows}, limit)
        n, avg = self.c.execute("SELECT COUNT(*),AVG(length) FROM ee_objects WHERE ns=? "
                                "AND active>0 AND length>0", (self.ns,)).fetchone()
        if not n:
            return {}
        avg = float(avg)
        scores = {}
        for term in query_terms:
            rows = self.c.execute("SELECT p.object_id,p.tf,o.length FROM ee_postings p JOIN ee_objects o "
                                  "ON o.ns=p.ns AND o.id=p.object_id WHERE p.ns=? AND p.term=? AND o.active>0",
                                  (self.ns, term)).fetchall()
            df = len(rows)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for oid, tf, length in rows:
                scores[oid] = scores.get(oid, 0) + idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
        ranked = dict(sorted(scores.items(), key=lambda x: (-x[1], x[0])))
        return self._cap_source_candidates(ranked, limit)

    def _cap_source_candidates(self, scores: dict[str, float], limit: int, per_source: int = 3):
        """A long source may contribute several spans, but cannot fill a whole retrieval arm."""
        ordered = sorted(scores, key=lambda oid: (-scores[oid], oid))[:max(limit * 64, limit)]
        objects = {oid: row for oid, row in zip(ordered, self._objects(ordered))}
        selected, counts = {}, {}
        for oid in ordered:
            kind, payload = objects.get(oid, ("", ""))
            source_id = json.loads(payload).get("source_id", oid) if kind == "span" and payload else oid
            if counts.get(source_id, 0) >= per_source:
                continue
            selected[oid] = scores[oid]
            counts[source_id] = counts.get(source_id, 0) + 1
            if len(selected) >= limit:
                break
        return selected

    def dense(self, vector, limit: int) -> dict[str, float]:
        vector = np.asarray(vector, dtype=np.float32)
        if self.repo.pg:
            self.c.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            self.c.execute("SELECT set_config('hnsw.max_scan_tuples',?,true)",
                           (str(self.repo.config.pg_hnsw_max_scan_tuples),))
            self.c.execute("SELECT set_config('hnsw.ef_search',?,true)",
                           (str(self.repo.config.pg_hnsw_ef_search),))
            literal = json.dumps(vector.tolist())
            rows = self.c.execute("WITH ann AS MATERIALIZED ("
                                  "SELECT v.id,v.embedding <=> ?::vector AS distance FROM ee_vectors v "
                                  "JOIN ee_objects o ON o.ns=v.ns AND o.id=v.id "
                                  "WHERE v.ns=? AND o.active>0 ORDER BY v.embedding <=> ?::vector LIMIT ?), "
                                  "ranked AS (SELECT a.id,1-a.distance AS score,ROW_NUMBER() OVER (PARTITION BY "
                                  "CASE WHEN o.kind='span' THEN o.payload::jsonb->>'source_id' ELSE o.id END "
                                  "ORDER BY a.distance,a.id) AS source_rank FROM ann a JOIN ee_objects o "
                                  "ON o.ns=? AND o.id=a.id AND o.active>0) "
                                  "SELECT id,score FROM ranked WHERE source_rank<=3 "
                                  "ORDER BY score DESC,id LIMIT ?",
                                  (literal, self.ns, literal, min(10000, max(limit * 32, limit)), self.ns, limit)).fetchall()
            return {oid: float(score) for oid, score in rows}
        rows = self.c.execute("SELECT v.id,v.embedding FROM ee_vectors v JOIN ee_objects o "
                              "ON o.ns=v.ns AND o.id=v.id WHERE v.ns=? AND o.active>0", (self.ns,)).fetchall()
        scores = [(oid, float(np.frombuffer(blob, dtype=np.float32) @ vector)) for oid, blob in rows]
        ranked = dict(sorted(scores, key=lambda x: (-x[1], x[0])))
        return self._cap_source_candidates(ranked, limit)

    def temporal(self, date: str | None, limit: int, *, date_end: str | None = None,
                 mode: str = "valid", query: str = "") -> dict[str, float]:
        """Retrieve time-indexed candidates while keeping event, validity, mention and record clocks apart."""
        columns = {"valid": ("valid_start", "valid_end", "interval"),
                   "occurrence": ("occurrence_start", "occurrence_end", "interval"),
                   "mention": ("mention_start", None, "point"),
                   "asserted": ("asserted_at", None, "timestamp"),
                   "stored": ("stored_at", None, "timestamp")}
        if mode not in columns:
            raise ValueError("unsupported temporal retrieval mode")
        start_col, end_col, semantics = columns[mode]
        if date and not date_end:
            date_end = date
        if not date:
            if not query:
                return {}
            lexical = self.lexical(query, min(max(limit * 8, limit), self.repo.config.graph_budget))
            if not lexical:
                return {}
            ids = list(lexical)
            placeholders = ",".join("?" for _ in ids)
            rows = self.c.execute(f"SELECT id,{start_col} FROM ee_objects WHERE ns=? AND active>0 "
                                  f"AND {start_col} IS NOT NULL AND id IN ({placeholders})",
                                  (self.ns, *ids)).fetchall()
            present = {oid: stamp for oid, stamp in rows}
            ranked = [oid for oid in ids if oid in present]
            if mode in {"occurrence", "valid"}:
                ranked.sort(key=lambda oid: (present[oid], oid), reverse=True)
            return {oid: 1 / (i + 1) for i, oid in enumerate(ranked[:limit])}
        source_group = ("CASE WHEN kind='span' THEN payload::jsonb->>'source_id' ELSE id END"
                        if self.repo.pg else
                        "CASE WHEN kind='span' THEN json_extract(payload,'$.source_id') ELSE id END")
        if semantics == "interval":
            predicate = f"{start_col}<=? AND ({end_col} IS NULL OR {end_col}>=?)"
            params = (date_end, date)
        elif semantics == "point":
            predicate = f"{start_col}>=? AND {start_col}<=?"
            params = (date, date_end)
        else:
            predicate = f"{start_col}>=? AND {start_col}<?"
            params = (date, (date_end or date) + "\uffff")
        rows = self.c.execute("WITH ranked AS (SELECT id," + start_col + " AS stamp,ROW_NUMBER() OVER (PARTITION BY "
                              + source_group + " ORDER BY " + start_col + " DESC,id) AS source_rank "
                              "FROM ee_objects WHERE ns=? AND active>0 AND " + predicate + ") "
                              "SELECT id FROM ranked WHERE source_rank<=3 ORDER BY stamp DESC,id LIMIT ?",
                              (self.ns, *params, limit)).fetchall()
        return {row[0]: 1 / (i + 1) for i, row in enumerate(rows)}

    def _by_ids(self, statement: str, ids):
        """Bounded IN batches keep snapshot materialization independent of candidate count RTTs."""
        ordered = sorted(set(ids))
        rows = []
        for start in range(0, len(ordered), 400):
            block = ordered[start:start + 400]
            placeholders = ",".join("?" for _ in block)
            rows.extend(self.c.execute(statement.format(ids=placeholders), (self.ns, *block)).fetchall())
        return rows

    def _objects(self, ids):
        return self._by_ids("SELECT kind,payload FROM ee_objects WHERE ns=? AND active>0 AND id IN ({ids})", ids)

    def _edges_for(self, ids, limit=None):
        rows = {}
        for field in ("origin", "target"):
            for payload, in self._by_ids("SELECT payload FROM ee_edges WHERE ns=? AND " + field + " IN ({ids})", ids):
                edge = Edge(**json.loads(payload))
                rows[edge.id] = edge
        result = [rows[key] for key in sorted(rows)]
        return result[:limit] if limit is not None else result

    def snapshot(self, ids: set[str] | None = None, expand: int = 0) -> Snapshot:
        if ids is None:
            rows = self.c.execute("SELECT kind,payload FROM ee_objects WHERE ns=? AND active>0", (self.ns,)).fetchall()
        else:
            ids, frontier = set(ids), set(ids)
            for _ in range(expand):
                edges = self._edges_for(frontier, self.repo.config.graph_budget)
                frontier = {oid for edge in edges for oid in (edge.origin, edge.target)} - ids
                ids.update(frontier)
                if not frontier:
                    break
            source_ids = {row[0] for row in self._by_ids(
                "SELECT source_id FROM ee_dependencies WHERE ns=? AND object_id IN ({ids})", ids)}
            rows = self._objects(ids | source_ids)
            source_ids.update(json.loads(payload)["id"] for kind, payload in rows if kind == "source")
            rows.extend(self._by_ids(
                "SELECT o.kind,o.payload FROM ee_objects o JOIN ee_dependencies d "
                "ON d.ns=o.ns AND d.object_id=o.id WHERE d.ns=? AND o.active>0 AND o.kind<>'span' "
                "AND d.source_id IN ({ids})", source_ids))
        sources, nodes, spans = {}, {}, {}
        for kind, payload in rows:
            data = json.loads(payload)
            if kind == "source":
                sources[data["id"]] = Source(**data)
            elif kind == "span":
                spans[data["id"]] = Span(**data)
            elif kind.startswith("coding_") or kind.startswith("multimodal_"):
                continue
            else:
                nodes[data["id"]] = node_from_dict(data)
        missing = {sid for node in nodes.values() for sid in node.source_ids} - sources.keys()
        for kind, payload in self._objects(missing):
            if kind == "source":
                source = Source(**json.loads(payload))
                sources[source.id] = source
        nodes = {oid: node for oid, node in nodes.items() if set(node.source_ids) <= sources.keys()}
        candidate_edges = [edge for edge in self._edges_for(nodes) if edge.origin in nodes and edge.target in nodes]
        missing = {sid for edge in candidate_edges for sid in edge.source_ids} - sources.keys()
        for kind, payload in self._objects(missing):
            if kind == "source":
                source = Source(**json.loads(payload))
                sources[source.id] = source
        edges = [edge for edge in candidate_edges if set(edge.source_ids) <= sources.keys()]
        spans = {oid: span for oid, span in spans.items() if span.source_id in sources}
        return Snapshot(self.generation, self.policy_generation, sources, nodes, edges, spans)
