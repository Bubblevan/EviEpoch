"""Source deletion closes the coding derivation, not only the message row."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.models import AddRequest, Message, SearchRequest
from eviepoch.pipeline import MemoryPipeline
from tests import test_lifecycle as fixtures

ORG = "git@example.com:org/service.git"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TOKEN = "eraseddifftoken9f3c2a"
KEEP = "keptlivingtoken9f3c2b"
HOP = "hopderivedtoken9f3c2c"
ORPHAN = "orphansymboltoken9f3c2d"
BLOB = "blobsharedtoken9f3c2e"
PATH = "pkg/erased_only_9f3c.py"
KEEP_PATH = "pkg/kept_handler.py"
SHARED_BLOB = "blob-shared9f3c2eaaaaaaaaaaaaaaaa"


def _diff(commit: str, path: str, token: str, padding: str = "") -> str:
    return (
        f"repository: {ORG}\n"
        f"commit: {commit}\n"
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n"
        "-old_line\n"
        f"+return {token}(){padding}\n"
        f"pytest {path}\n"
        f"FAILED {path}::test_case - {token}\n"
        "exit code: 1\n"
    )


class CodingDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.provider = fixtures.AnnotatedProviders()
        self.pipeline = MemoryPipeline(
            Settings(
                task_adapter="coding",
                db_path=str(Path(self.temp.name) / "coding.sqlite3"),
                llm_api_key="test",
                embed_api_key="test",
                embed_base_url="https://example.test/v1",
                embed_dimensions=32,
                auth_scheme="none",
                rerank_enabled=False,
                window_radius=0,
            ),
            self.provider,
        )
        self.pipeline.start()

    def tearDown(self) -> None:
        self.pipeline.close()
        self.temp.cleanup()

    def _add(self, request_id: str, user: str, content: str, role: str = "tool") -> AddRequest:
        self.provider.register(content, nodes=())
        request = AddRequest(request_id, user, "sess", [Message(role, content, 1700000000000)])
        self.pipeline.add(request)
        return request

    def _forget(self, user: str, token: str, action: str) -> None:
        instruction = f"Please forget {token}."

        def extraction(known):
            targets = [sid for sid, source in known.sources.items() if token in source.content]
            return {
                "nodes": [],
                "relations": [],
                "governance": [{
                    "message": 0,
                    "quote": instruction,
                    "action": action,
                    "target_ids": targets,
                }],
            }

        self.provider.documents[instruction] = extraction
        self.pipeline.add(AddRequest(f"forget-{user}-{action}", user, "sess", [Message("user", instruction)]))

    def _search(self, user: str, query: str) -> str:
        result = self.pipeline.search(SearchRequest(query=query, user_id=user, top_k=8))
        return json.dumps(result, ensure_ascii=False)

    def _stored_text(self, user: str) -> str:
        ns = namespace(user)
        parts: list[str] = []
        with self.pipeline.store.transaction(ns) as c:
            for table in (
                "ee_repositories", "ee_code_snapshots", "ee_file_revisions", "ee_symbol_revisions",
                "ee_engineering_events", "ee_patch_attempts", "ee_test_runs", "ee_experiences",
                "ee_applicability_assessments", "ee_coding_blobs",
            ):
                for (payload,) in c.execute(f"SELECT payload FROM {table} WHERE ns=?", (ns,)).fetchall():
                    parts.append(payload or "")
            for (evidence,) in c.execute("SELECT evidence FROM ee_coding_edges WHERE ns=?", (ns,)).fetchall():
                parts.append(evidence or "")
            for body, payload in c.execute("SELECT body, payload FROM ee_objects WHERE ns=?", (ns,)).fetchall():
                parts.append(body or "")
                parts.append(payload or "")
            for (payload,) in c.execute("SELECT payload FROM ee_edges WHERE ns=?", (ns,)).fetchall():
                parts.append(payload or "")
            for (payload,) in c.execute("SELECT payload FROM ee_operations WHERE ns=?", (ns,)).fetchall():
                parts.append(payload or "")
        return "\n".join(parts)

    def test_erase_closes_transitive_coding_rows_and_keeps_other_copies(self) -> None:
        original = self._add("erase-source", "u", _diff(COMMIT_A, PATH, TOKEN))
        self._add("keep-source", "u", _diff(COMMIT_B, KEEP_PATH, KEEP))
        self._add("other-source", "other", _diff(COMMIT_A, PATH, TOKEN))
        self.assertIn(TOKEN, self._search("u", TOKEN))
        self.assertIn(TOKEN, self._stored_text("u"))

        ns = namespace("u")
        hop_id = "hop-derived-9f3c"
        orphan_id = "orphan-symbol-9f3c"
        with self.pipeline.store.transaction(ns, True) as c:
            snapshot_id = next(
                sid for sid, payload in c.execute(
                    "SELECT snapshot_id, payload FROM ee_code_snapshots WHERE ns=?", (ns,)
                ).fetchall()
                if COMMIT_A in (payload or "")
            )
            patch_id = next(
                oid for oid, payload in c.execute(
                    "SELECT id, payload FROM ee_objects WHERE ns=? AND kind='coding_patch_attempt'", (ns,)
                ).fetchall()
                if TOKEN in (payload or "")
            )
            c.execute(
                "INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                "predicate_key,scope_key,length) VALUES(?,?,?,?,?,NULL,1,1,'','','',?)",
                (ns, hop_id, "coding_note", json.dumps({"blob_id": SHARED_BLOB, "note": HOP}), HOP, len(HOP)),
            )
            c.execute(
                "INSERT INTO ee_dependencies(ns,object_id,source_id) VALUES(?,?,?)",
                (ns, hop_id, patch_id),
            )
            c.execute(
                "INSERT INTO ee_postings(ns,term,object_id,tf) VALUES(?,?,?,1)",
                (ns, HOP, hop_id),
            )
            c.execute(
                "INSERT INTO ee_symbol_revisions(ns,symbol_id,snapshot_id,file_id,qualified_name,generation,payload) "
                "VALUES(?,?,?,?,?,1,?)",
                (ns, orphan_id, snapshot_id, "orphan-file", ORPHAN, json.dumps({"name": ORPHAN})),
            )
            c.execute(
                "INSERT INTO ee_objects(ns,id,kind,payload,body,asserted_at,generation,active,subject_key,"
                "predicate_key,scope_key,length) VALUES(?,?,?,?,?,NULL,1,1,'','','',?)",
                (
                    ns, orphan_id, "coding_symbol_revision", json.dumps({"name": ORPHAN}),
                    ORPHAN, len(ORPHAN),
                ),
            )
            c.execute(
                "INSERT INTO ee_postings(ns,term,object_id,tf) VALUES(?,?,?,1)",
                (ns, ORPHAN, orphan_id),
            )
            for owner in (ns, namespace("other")):
                c.execute(
                    "INSERT INTO ee_coding_blobs(ns,blob_id,content_hash,byte_length,payload,generation) "
                    "VALUES(?,?,?,?,?,?)",
                    (owner, SHARED_BLOB, "hash", len(TOKEN), json.dumps({"blob_id": SHARED_BLOB, "body": TOKEN}), 1),
                )

        self._forget("u", TOKEN, "erase")
        self.pipeline.add(original)

        self.assertNotIn(TOKEN, self._search("u", f"repository: {ORG} commit: {COMMIT_A} path: {PATH} {TOKEN}"))
        self.assertNotIn(HOP, self._search("u", HOP))
        self.assertNotIn(ORPHAN, self._search("u", ORPHAN))
        self.assertNotIn(TOKEN, self._stored_text("u"))
        self.assertNotIn(HOP, self._stored_text("u"))
        self.assertNotIn(ORPHAN, self._stored_text("u"))
        self.assertIn(KEEP, self._search("u", KEEP))
        self.assertIn(TOKEN, self._search("other", TOKEN))
        self.assertIn(TOKEN, self._stored_text("other"))

        with self.pipeline.store.transaction(ns) as c:
            self.assertIsNone(c.execute(
                "SELECT snapshot_id FROM ee_code_snapshots WHERE ns=? AND payload LIKE ?",
                (ns, f"%{COMMIT_A}%"),
            ).fetchone())
            self.assertIsNone(c.execute(
                "SELECT symbol_id FROM ee_symbol_revisions WHERE ns=? AND symbol_id=?",
                (ns, orphan_id),
            ).fetchone())
            self.assertIsNone(c.execute(
                "SELECT blob_id FROM ee_coding_blobs WHERE ns=? AND blob_id=?",
                (ns, SHARED_BLOB),
            ).fetchone())
            self.assertGreater(c.execute("SELECT COUNT(*) FROM ee_repositories WHERE ns=?", (ns,)).fetchone()[0], 0)
            self.assertGreater(
                c.execute("SELECT COUNT(*) FROM ee_tombstones WHERE ns=? AND action='erase'", (ns,)).fetchone()[0], 0,
            )
        with self.pipeline.store.transaction(namespace("other")) as c:
            self.assertIsNotNone(c.execute(
                "SELECT blob_id FROM ee_coding_blobs WHERE ns=? AND blob_id=?",
                (namespace("other"), SHARED_BLOB),
            ).fetchone())

    def test_suppress_blocks_coding_assembly_without_erasing_the_other_namespace(self) -> None:
        self._add("suppress-source", "u", _diff(COMMIT_A, PATH, TOKEN))
        self._add("suppress-other", "other", _diff(COMMIT_A, PATH, TOKEN))
        self.assertIn(TOKEN, self._search("u", TOKEN))
        self._forget("u", TOKEN, "suppress")
        hidden = self._search("u", f"repository: {ORG} commit: {COMMIT_A} path: {PATH} symbol: {TOKEN} {TOKEN}")
        self.assertNotIn(TOKEN, hidden)
        self.assertIn(TOKEN, self._search("other", TOKEN))
        ns = namespace("u")
        with self.pipeline.store.transaction(ns) as c:
            payload = c.execute(
                "SELECT payload FROM ee_patch_attempts WHERE ns=? AND payload LIKE ?",
                (ns, f"%{TOKEN}%"),
            ).fetchone()
            self.assertIsNotNone(payload)
            active = c.execute(
                "SELECT active FROM ee_objects WHERE ns=? AND kind='coding_patch_attempt' AND payload LIKE ?",
                (ns, f"%{TOKEN}%"),
            ).fetchone()
            self.assertEqual(active[0], 0)
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM ee_postings WHERE ns=? AND term=?", (ns, TOKEN)).fetchone()[0], 0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM ee_vectors v JOIN ee_objects o ON o.ns=v.ns AND o.id=v.id "
                    "WHERE v.ns=? AND o.payload LIKE ?",
                    (ns, f"%{TOKEN}%"),
                ).fetchone()[0],
                0,
            )

    def test_erase_removes_only_this_namespace_blob(self) -> None:
        padding = "x" * 70000
        self._add("blob-a", "u", _diff(COMMIT_A, PATH, BLOB, padding))
        self._add("blob-b", "other", _diff(COMMIT_A, PATH, BLOB, padding))
        ns = namespace("u")
        other = namespace("other")
        with self.pipeline.store.transaction(ns) as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM ee_coding_blobs WHERE ns=?", (ns,)).fetchone()[0], 0)
        with self.pipeline.store.transaction(other) as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM ee_coding_blobs WHERE ns=?", (other,)).fetchone()[0], 0)
        self._forget("u", BLOB, "erase")
        self.assertNotIn(BLOB, self._search("u", BLOB))
        self.assertNotIn(BLOB, self._stored_text("u"))
        self.assertIn(BLOB, self._search("other", BLOB))
        with self.pipeline.store.transaction(ns) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ee_coding_blobs WHERE ns=?", (ns,)).fetchone()[0], 0)
        with self.pipeline.store.transaction(other) as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM ee_coding_blobs WHERE ns=?", (other,)).fetchone()[0], 0)
            self.assertIn(BLOB, c.execute(
                "SELECT payload FROM ee_coding_blobs WHERE ns=?", (other,)
            ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
