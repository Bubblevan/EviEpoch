
"""
Helper functions for hybrid search (semantic + BM25 + graph).
"""
from dataclasses import dataclass,field
from datetime import datetime
from typing import Any

@dataclass
class RetrievalResult:
    """
    Result from a single retrieval method (semantic, BM25, graph, or temporal).

    This represents a raw result from the database query, before merging or reranking.
    """

    id: str
    text: str
    fact_type: str
    context: str | None = None
    event_date: datetime | None = None
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    mentioned_at: datetime | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    tags: list[str] | None = None  # Visibility scope tags
    metadata: dict[str, str] | None = None  # User-provided metadata
    proof_count: int | None = None  # Number of supporting memories (observations only)

    # Entity postings the backend already resolved for this unit, if any.
    # ``None`` means "this backend does not carry entity ids on the result" (the default
    # store, which resolves them later via ``entity_map_for_units``); a list — possibly
    # empty — means the backend already resolved the unit->entity posting inline, so
    # recall can build the entity map directly instead of re-fetching the memories.
    #
    # CONTRACT: a backend that populates this for an OBSERVATION MUST include the
    # entities it inherits from its source memories, not only any it carries directly.
    # Recall builds the entity map straight from this list and does NOT resolve
    # observation-from-source inheritance itself (the default store, which leaves this
    # ``None``, resolves that inheritance inside ``entity_map_for_units`` instead). A
    # backend that owns its index and resolves the inheritance at write time — so the
    # stored record's entity ids are already the complete set — satisfies this; one that
    # only stores direct postings must leave this ``None`` for observations.
    entity_ids: list[str] | None = None

    # The memories an OBSERVATION was consolidated from, if the backend carried them.
    # ``None`` means "not carried" (the default store, and every non-observation result);
    # a list — possibly empty — means the backend resolved it inline.
    #
    # Recall needs this twice for an observation it is about to return: ``prefer_observations``
    # drops the raw facts an observation supersedes, and ``include_chunks`` walks the sources for
    # their chunk ids. Both used to re-fetch the observation to read one field off a record the
    # hydration had already fetched and thrown away. A store that leaves this ``None`` keeps the
    # re-fetch, so the two paths stay interchangeable rather than one being an approximation.
    source_memory_ids: list[str] | None = None

    # Short ids of the attachments this fact was drawn from, if the backend carried them.
    # ``None`` means "not carried" (the default store, which reads them back from
    # ``memory_units.attachment_ids`` when the response is rendered); a list — possibly
    # empty — means the backend returned them on the row, so the read surface resolves
    # them without asking the store again.
    attachment_ids: list[str] | None = None

    # Retrieval-specific scores (only one will be set depending on retrieval method)
    similarity: float | None = None  # Semantic retrieval
    bm25_score: float | None = None  # BM25 retrieval
    activation: float | None = None  # Graph retrieval (spreading activation)
    temporal_score: float | None = None  # Temporal retrieval
    temporal_proximity: float | None = None  # Temporal retrieval

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "RetrievalResult":
        """Create from a database row (asyncpg Record converted to dict)."""
        return cls(
            id=str(row["id"]),
            text=row["text"],
            fact_type=row["fact_type"],
            context=row.get("context"),
            event_date=row.get("event_date"),
            occurred_start=row.get("occurred_start"),
            occurred_end=row.get("occurred_end"),
            mentioned_at=row.get("mentioned_at"),
            document_id=row.get("document_id"),
            chunk_id=row.get("chunk_id"),
            tags=row.get("tags"),
            metadata=row.get("metadata"),
            proof_count=row.get("proof_count"),
            similarity=row.get("similarity"),
            bm25_score=row.get("bm25_score"),
            activation=row.get("activation"),
            temporal_score=row.get("temporal_score"),
            temporal_proximity=row.get("temporal_proximity"),
        )


@dataclass
class ArmScores:
    """Raw per-strategy retrieval scores for a single doc, aggregated across arms.

    Fusion keeps only the first-seen RetrievalResult per doc, so its per-arm score
    fields reflect just one arm. This captures each arm's raw score for the same doc
    so the recall response can report them (and ``min_scores`` can filter on them).
    ``None`` means the doc was not surfaced by that arm.
    """

    semantic: float | None = None  # cosine similarity from the semantic arm
    keyword: float | None = None  # BM25 / full-text score from the keyword arm


@dataclass
class MergedCandidate:
    """
    Candidate after RRF merge of multiple retrieval results.

    Contains the original retrieval data plus RRF metadata.
    """

    # Original retrieval data
    retrieval: RetrievalResult

    # RRF metadata
    rrf_score: float
    rrf_rank: int = 0
    source_ranks: dict[str, int] = field(default_factory=dict)  # method_name -> rank
    arm_scores: "ArmScores" = field(default_factory=lambda: ArmScores())  # raw per-strategy scores

    @property
    def id(self) -> str:
        """Convenience property to access ID."""
        return self.retrieval.id


def cap_per_source(results: list[RetrievalResult], cap: int) -> list[RetrievalResult]:
    """Truncate a single retrieval arm to its top-``cap`` results.

    Applied per source (semantic, BM25, graph, temporal) before fusion so that
    one over-expanding backend cannot crowd out the others when the merged pool
    is later trimmed to the reranker's global candidate budget. The caller is
    responsible for sorting ``results`` by relevance first; this only slices.

    Args:
        results: Results for a single source, already sorted best-first.
        cap: Maximum results to keep. ``0`` (or negative) disables the cap.

    Returns:
        The original list when the cap is disabled or not exceeded, otherwise a
        truncated copy of the top ``cap`` results.
    """
    if cap <= 0 or len(results) <= cap:
        return results
    return results[:cap]


_SOURCE_NAMES = ("semantic", "bm25", "graph", "temporal")


def _source_name(index: int) -> str:
    return _SOURCE_NAMES[index] if index < len(_SOURCE_NAMES) else f"source_{index}"


def _collect_ranked_results(result_lists: list[list[RetrievalResult]]):
    """Validate arms and collect first-seen, per-document fusion metadata."""
    ranked_lists: list[list[tuple[int, RetrievalResult]]] = []
    retrievals: dict[str, RetrievalResult] = {}
    source_ranks: dict[str, dict[str, int]] = {}
    arm_scores: dict[str, ArmScores] = {}

    for source_idx, results in enumerate(result_lists):
        source_name = _source_name(source_idx)
        ranked: list[tuple[int, RetrievalResult]] = []
        seen_in_arm: set[str] = set()

        for rank, retrieval in enumerate(results, start=1):
            if isinstance(retrieval, tuple):
                raise TypeError(
                    f"Expected RetrievalResult but got tuple in {source_name} results at rank {rank}. "
                    f"Tuple value: {retrieval[:2] if len(retrieval) >= 2 else retrieval}. "
                    "This suggests the retrieval function returned tuples instead of RetrievalResult objects."
                )
            if not isinstance(retrieval, RetrievalResult):
                raise TypeError(
                    f"Expected RetrievalResult but got {type(retrieval).__name__} "
                    f"in {source_name} results at rank {rank}"
                )

            doc_id = retrieval.id
            retrievals.setdefault(doc_id, retrieval)
            if doc_id in seen_in_arm:
                continue
            seen_in_arm.add(doc_id)
            ranked.append((rank, retrieval))
            source_ranks.setdefault(doc_id, {})[f"{source_name}_rank"] = rank

            scores = arm_scores.setdefault(doc_id, ArmScores())
            if source_name == "semantic" and retrieval.similarity is not None:
                scores.semantic = retrieval.similarity
            elif source_name == "bm25" and retrieval.bm25_score is not None:
                scores.keyword = retrieval.bm25_score

        ranked_lists.append(ranked)

    return ranked_lists, retrievals, source_ranks, arm_scores


def reciprocal_rank_fusion(result_lists: list[list[RetrievalResult]], k: int = 60) -> list[MergedCandidate]:
    """
    Merge multiple ranked result lists using Reciprocal Rank Fusion.

    RRF formula: score(d) = sum_over_lists(1 / (k + rank(d)))

    Args:
        result_lists: List of result lists, each containing RetrievalResult objects
        k: Constant for RRF formula (default: 60)

    Returns:
        Merged list of MergedCandidate objects, sorted by RRF score

    Example:
        semantic_results = [RetrievalResult(...), RetrievalResult(...), ...]
        bm25_results = [RetrievalResult(...), RetrievalResult(...), ...]
        graph_results = [RetrievalResult(...), RetrievalResult(...), ...]

        merged = reciprocal_rank_fusion([semantic_results, bm25_results, graph_results])
        # Returns: [MergedCandidate(...), MergedCandidate(...), ...]
    """
    if k < 0:
        raise ValueError("k must be non-negative")

    ranked_lists, all_retrievals, source_ranks, arm_scores = _collect_ranked_results(result_lists)
    rrf_scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, retrieval in ranked:
            rrf_scores[retrieval.id] = rrf_scores.get(retrieval.id, 0.0) + 1.0 / (k + rank)

    # Combine into final results with metadata
    merged_results = []
    for rrf_rank, (doc_id, rrf_score) in enumerate(
        sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True), start=1
    ):
        merged_candidate = MergedCandidate(
            retrieval=all_retrievals[doc_id],
            rrf_score=rrf_score,
            rrf_rank=rrf_rank,
            source_ranks=source_ranks[doc_id],
            arm_scores=arm_scores[doc_id],
        )
        merged_results.append(merged_candidate)

    return merged_results


def interleave_fusion(result_lists: list[list[RetrievalResult]]) -> list[MergedCandidate]:
    """Round-robin (interleaved) fusion — an alternative to RRF for dedup-style recall.

    RRF scores a doc by the *sum* of its reciprocal ranks across arms, so a result
    that is #1 in one arm but absent/low in the others gets averaged down. That is
    exactly the consolidation-dedup failure mode: the near-identical existing
    observation (the "twin" to merge into) is semantic rank #1, yet shares no
    source-fact graph link and little lexical overlap, so RRF drops it below the
    recall budget cutoff and the LLM never sees it → creates a duplicate.

    Interleave instead *guarantees every arm's top hits a slot*: take each arm's
    #1, then each arm's #2, … in arm-priority order, de-duplicating, until all
    results are placed. The arm priority is the order of ``result_lists``
    (semantic, bm25, graph, temporal), so semantic #1 is always first.

    ``rrf_score`` is assigned strictly decreasing by final interleave position so
    downstream order-by-score sorts preserve the interleave order; ``source_ranks``
    mirrors the RRF bookkeeping (each doc's rank within every arm it appears in).
    """
    ranked_lists, all_retrievals, source_ranks, arm_scores = _collect_ranked_results(result_lists)

    # Round-robin pick across arms in priority order: all #1s, then all #2s, ...
    ordered_ids: list[str] = []
    seen: set[str] = set()
    max_len = max((len(r) for r in ranked_lists), default=0)
    for r in range(max_len):
        for results in ranked_lists:
            if r < len(results):
                doc_id = results[r][1].id
                if doc_id not in seen:
                    seen.add(doc_id)
                    ordered_ids.append(doc_id)

    n = len(ordered_ids)
    return [
        MergedCandidate(
            retrieval=all_retrievals[doc_id],
            # Strictly decreasing by interleave position → sorting desc by rrf_score
            # reproduces the interleave order downstream.
            rrf_score=float(n - pos),
            rrf_rank=pos + 1,
            source_ranks=source_ranks[doc_id],
            arm_scores=arm_scores[doc_id],
        )
        for pos, doc_id in enumerate(ordered_ids)
    ]


def merge(channels: list[dict[str, float]], k: int = 60, cap: int = 0, names=None):
    ranked = []
    for i, channel in enumerate(channels):
        rows = [RetrievalResult(id=oid, text="", fact_type="navigation", similarity=score if i % 2 == 0 else None,
                                bm25_score=score if i % 2 else None) for oid, score in channel.items()]
        ranked.append(cap_per_source(rows, cap))
    merged = reciprocal_rank_fusion(ranked, k)
    ranks = {row.id: row.source_ranks for row in merged}
    if names:
        mapping = {
            f"{_source_name(i)}_rank": f"{names[i] if i < len(names) and names[i] else _source_name(i)}_rank"
            for i in range(len(channels))
        }
        ranks = {oid: {mapping[key]: value for key, value in fields.items()} for oid, fields in ranks.items()}
    return {row.id: row.rrf_score for row in merged}, ranks
