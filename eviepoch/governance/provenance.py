from __future__ import annotations

import json
from typing import Any, NamedTuple
from uuid import UUID

from ..domain import stable_id


SOURCE_REF_PREFIX = "source_ref:v1"
CHUNK_SOURCE_REF_PREFIX = "source_ref:v2"
SOURCE_RUN_REF_PREFIX = "source_run_ref:v1"

GRAPH_PROVENANCE_VERSION_KEY = "provenance_version"
GRAPH_DELETE_MODE_KEY = "delete_mode"
GRAPH_PROVENANCE_VERSION = "1"
GRAPH_DELETE_MODE_GRAPH_PROVENANCE = "graph_native"


class ParsedSourceRef(NamedTuple):
    version: int
    dataset_id: UUID
    data_id: UUID
    chunk_id: UUID | None


class ProvenanceColumns(NamedTuple):
    source_ref_keys: list[str]
    source_dataset_ids: list[str]
    source_run_ids: list[str]
    source_run_refs: list[str]


class ProvenanceAttachInputs(NamedTuple):
    source_ref_key: str
    add_keys: list[str]
    add_dataset_ids: list[str]
    add_run_refs: list[str]
    add_run_ids: list[str]


def _ordered_unique(values: list[str]) -> list[str]:
    """Return first occurrences in order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def make_source_ref_key(dataset_id: UUID, data_id: UUID) -> str:
    return f"{SOURCE_REF_PREFIX}:{dataset_id}:{data_id}"


def make_chunk_source_ref_key(dataset_id: UUID, data_id: UUID, chunk_id: UUID) -> str:
    return f"{CHUNK_SOURCE_REF_PREFIX}:{dataset_id}:{data_id}:{chunk_id}"


def parse_source_ref_key(source_ref_key: str) -> ParsedSourceRef:
    parts = source_ref_key.split(":")
    try:
        if len(parts) == 4 and ":".join(parts[:2]) == SOURCE_REF_PREFIX:
            return ParsedSourceRef(1, UUID(parts[2]), UUID(parts[3]), None)
        if len(parts) == 5 and ":".join(parts[:2]) == CHUNK_SOURCE_REF_PREFIX:
            return ParsedSourceRef(2, UUID(parts[2]), UUID(parts[3]), UUID(parts[4]))
    except (AttributeError, TypeError, ValueError):
        pass
    raise ValueError("Unsupported source ref key format")


def get_dataset_id_from_source_ref_key(source_ref_key: str) -> UUID:
    return parse_source_ref_key(source_ref_key).dataset_id


def get_data_id_from_source_ref_key(source_ref_key: str) -> UUID:
    return parse_source_ref_key(source_ref_key).data_id


def make_source_run_ref(pipeline_run_id: UUID, source_ref_key: str) -> str:
    return f"{SOURCE_RUN_REF_PREFIX}:{pipeline_run_id}:{source_ref_key}"


def _split_source_run_ref(source_run_ref: str) -> tuple[UUID, str]:
    parts = source_run_ref.split(":", 3)
    if len(parts) != 4 or ":".join(parts[:2]) != SOURCE_RUN_REF_PREFIX:
        raise ValueError("Unsupported source run ref format")
    try:
        run_id = UUID(parts[2])
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Unsupported source run ref format") from exc
    return run_id, parts[3]


def get_pipeline_run_id_from_source_run_ref(source_run_ref: str) -> UUID:
    return _split_source_run_ref(source_run_ref)[0]


def get_source_ref_key_from_source_run_ref(source_run_ref: str) -> str:
    return _split_source_run_ref(source_run_ref)[1]


def coerce_run_uuid(pipeline_run_id: Any) -> UUID:
    return pipeline_run_id if isinstance(pipeline_run_id, UUID) else UUID(str(pipeline_run_id))


def derive_dataset_ids(source_ref_keys: list[str]) -> list[str]:
    return sorted({str(get_dataset_id_from_source_ref_key(key)) for key in source_ref_keys})


def derive_run_ids(source_run_refs: list[str]) -> list[str]:
    return sorted({str(get_pipeline_run_id_from_source_run_ref(ref)) for ref in source_run_refs})


def provenance_attach_inputs(
    source_ref_key: str,
    pipeline_run_id: str | None,
) -> ProvenanceAttachInputs:
    add_run_refs: list[str] = []
    add_run_ids: list[str] = []
    if pipeline_run_id is not None:
        run_id = coerce_run_uuid(pipeline_run_id)
        add_run_refs = [make_source_run_ref(run_id, source_ref_key)]
        add_run_ids = [str(run_id)]
    return ProvenanceAttachInputs(
        source_ref_key=source_ref_key,
        add_keys=[source_ref_key],
        add_dataset_ids=[str(get_dataset_id_from_source_ref_key(source_ref_key))],
        add_run_refs=add_run_refs,
        add_run_ids=add_run_ids,
    )


def provenance_after_attach(
    current_keys: list[str],
    current_run_refs: list[str],
    add_keys: list[str],
    pipeline_run_id: str | None,
) -> ProvenanceColumns:
    keys = _ordered_unique(current_keys)
    known_keys = set(keys)
    newly_added: list[str] = []
    for key in add_keys:
        if key not in known_keys:
            known_keys.add(key)
            newly_added.append(key)
    keys.extend(newly_added)

    run_refs = _ordered_unique(current_run_refs)
    if pipeline_run_id is not None:
        run_id = coerce_run_uuid(pipeline_run_id)
        for key in newly_added:
            run_ref = make_source_run_ref(run_id, key)
            if run_ref not in run_refs:
                run_refs.append(run_ref)

    return ProvenanceColumns(keys, derive_dataset_ids(keys), derive_run_ids(run_refs), run_refs)


def provenance_after_remove(
    current_keys: list[str],
    current_run_refs: list[str],
    remove_keys: list[str],
) -> ProvenanceColumns:
    removed = set(remove_keys)
    keys = _ordered_unique([key for key in current_keys if key not in removed])
    run_refs = _ordered_unique([
        ref for ref in current_run_refs
        if get_source_ref_key_from_source_run_ref(ref) not in removed
    ])
    return ProvenanceColumns(keys, derive_dataset_ids(keys), derive_run_ids(run_refs), run_refs)


def _load_payload(c, ns: str, oid: str) -> dict | None:
    row = c.execute("SELECT payload FROM ee_provenance WHERE ns=? AND id=?", (ns, oid)).fetchone()
    return json.loads(row[0]) if row else None


def _save_payload(c, ns: str, oid: str, payload: dict) -> None:
    c.execute(
        "INSERT INTO ee_provenance(ns,id,payload) VALUES(?,?,?) "
        "ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload",
        (ns, oid, json.dumps(payload)),
    )


def attach(c, ns: str, oid: str, sources, generation: int):
    current = _load_payload(c, ns, oid) or {"source_ref_keys": [], "source_run_refs": []}
    namespace_id = UUID(ns[:32])
    refs = [make_source_ref_key(namespace_id, UUID(source_id)) for source_id in sorted(set(sources))]
    transition = provenance_after_attach(
        current["source_ref_keys"], current["source_run_refs"], refs,
        stable_id(ns, "publication", generation),
    )
    _save_payload(c, ns, oid, transition._asdict())


def detach(c, ns: str, oid: str, source_ids: set[str]):
    current = _load_payload(c, ns, oid)
    if current is None:
        return
    namespace_id = UUID(ns[:32])
    removed = [make_source_ref_key(namespace_id, UUID(source_id)) for source_id in source_ids]
    transition = provenance_after_remove(current["source_ref_keys"], current["source_run_refs"], removed)
    if transition.source_ref_keys:
        _save_payload(c, ns, oid, transition._asdict())
    else:
        c.execute("DELETE FROM ee_provenance WHERE ns=? AND id=?", (ns, oid))
