"""Opt-in integration suite; requires a disposable database ending in _test."""
import os
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.pipeline import MemoryPipeline
from eviepoch.storage.repository import View
from test_lifecycle import AnnotatedProviders
import test_lifecycle

SERVICE = os.getenv("EPOCH_TEST_DATABASE_URL", "")
ADMIN = os.getenv("EPOCH_TEST_ADMIN_DATABASE_URL", "")


@unittest.skipUnless(SERVICE and ADMIN, "set disposable PostgreSQL test URLs to run integration cases")
class PostgresLifecycleTests(test_lifecycle.LifecycleTests):
    def setUp(self):
        import psycopg
        from eviepoch.storage.migrate import TABLES, migrate
        from psycopg import sql
        if not urlsplit(ADMIN).path.endswith("_test"):
            raise ValueError("Integration tests require a disposable database ending in _test")
        config = Settings(embed_dimensions=32, embed_model="contract-test", embed_base_url="https://example.test/v1")
        migrate(ADMIN, "eviepoch_app", config)
        with psycopg.connect(ADMIN) as conn:
            conn.execute(sql.SQL("TRUNCATE {} ").format(sql.SQL(",").join(map(sql.Identifier, TABLES))))
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.config = Settings(database_url=SERVICE, embed_dimensions=32, llm_api_key="test", embed_api_key="test",
                               embed_base_url="https://example.test/v1", auth_scheme="none", workbench_enabled=True)
        self.provider = AnnotatedProviders()
        self.pipeline = MemoryPipeline(self.config, self.provider)
        self.pipeline.start()

    def test_vectors_are_namespace_partitioned_with_local_hnsw_indexes(self):
        import psycopg
        with psycopg.connect(ADMIN) as conn:
            partition_key = conn.execute("SELECT pg_get_partkeydef('ee_vectors'::regclass)").fetchone()[0]
            partition_count = conn.execute("SELECT COUNT(*) FROM pg_inherits WHERE inhparent='ee_vectors'::regclass").fetchone()[0]
            hnsw_count = conn.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() "
                                      "AND indexname ~ '^ee_vectors_p[0-9]+_hnsw$'").fetchone()[0]
        self.assertEqual(partition_key, "HASH (ns)")
        self.assertEqual(partition_count, 32)
        self.assertEqual(hnsw_count, 32)

    def test_temporal_validity_interval_and_occurrence_columns(self):
        quote = "Alice joined Acme on 2020-02-03; the agreement is valid from 2021."
        self.add(quote, [test_lifecycle.fact(quote, "joined", predicate="employment event", kind="event",
                                            event_date="2020-02-03", time_quote="2020-02-03",
                                            valid_date="2021")], rid="pg-temporal-clocks",
                 timestamp=1577836800000)
        with self.pipeline.store.view(namespace("u")) as view:
            row = view.c.execute("SELECT valid_start,valid_end,occurrence_start,occurrence_end "
                                 "FROM ee_objects WHERE ns=? AND kind='event'",
                                 (namespace("u"),)).fetchone()
            occurrence = view.temporal("2020-02-03", 10, mode="occurrence")
            valid = view.temporal("2020-02-03", 10, mode="valid")
            live_midyear = view.temporal("2021-06-01", 10, mode="valid")
            after_validity = view.temporal("2022-01-01", 10, mode="valid")
        self.assertEqual(row, ("2021-01-01", "2021-12-31", "2020-02-03", "2020-02-03"))
        self.assertTrue(occurrence)
        self.assertFalse(valid)
        self.assertTrue(live_midyear)
        self.assertFalse(after_validity)

    def test_relative_event_query_retrieves_occurrences_without_as_of(self):
        quote = "Alice joined Acme on 2020-02-03."
        self.add(quote, [test_lifecycle.fact(quote, "joined", predicate="Acme employment", kind="event",
                                            event_date="2020-02-03", time_quote="2020-02-03")],
                 rid="pg-relative-event", timestamp=1577836800000)
        self.provider.plan_value = {"intents": ["fact"], "as_of": None,
                                    "obligations": [{"query": "Alice joined Acme"}]}
        calls = []
        original = View.temporal

        def tracked(view, date, limit, **kwargs):
            calls.append((date, kwargs.get("mode")))
            return original(view, date, limit, **kwargs)

        with patch.object(View, "temporal", tracked):
            self.pipeline.search(test_lifecycle.SearchRequest(
                "How many months after joining Acme did Alice move to Boston?", "u", 5))
        self.assertIn((None, "occurrence"), calls)
        calls.clear()
        with patch.object(View, "temporal", tracked):
            self.pipeline.search(test_lifecycle.SearchRequest("Where does Alice live?", "u", 5))
        self.assertFalse(any(mode == "occurrence" for _date, mode in calls))

    def test_graph_channel_finds_a_two_hop_path_before_dense_and_keyword_seeds(self):
        rows = [
            ("Alice joined Project X.", test_lifecycle.fact("Alice joined Project X.", "Project X",
                                                              subject="Alice", predicate="joined project")),
            ("Project X uses Database Y.", test_lifecycle.fact("Project X uses Database Y.", "Database Y",
                                                                 subject="Project X", predicate="uses database")),
            ("Database Y was replaced by Database Z.", test_lifecycle.fact(
                "Database Y was replaced by Database Z.", "Database Z", subject="Database Y",
                predicate="replaced by database")),
        ]
        for index, (content, node) in enumerate(rows):
            self.add(content, [node], rid=f"pg-entity-path-{index}")
        query = "Which database replaced the one used by Alice's project?"
        self.provider.plan_value = {"intents": ["fact"], "entities": [], "attributes": [],
                                    "obligations": [{"query": query}]}
        self.pipeline.config = replace(self.config, candidate_limit=16, graph_depth=2,
                                       graph_retrieval_enabled=True, rerank_enabled=False)
        with patch.object(View, "dense", lambda _view, _vector, _limit: {}), \
                patch.object(View, "lexical", lambda _view, _query, _limit: {}):
            _response, trace = self.pipeline.search_with_trace(test_lifecycle.SearchRequest(query, "u", 5))
        nodes = self.pipeline.inspect("u")["nodes"]
        terminal = next(node for node in nodes if node["subject"] == "Database Y")
        self.assertEqual(trace["retrieval_channels"]["graph"], 3)
        self.assertEqual(trace["channel_ranks"][terminal["id"]]["graph_rank"], 3)

    def test_graph_channel_does_not_join_conflicting_explicit_entity_ids_by_label(self):
        root = json.dumps({"subject": "Orion", "relation": "owns project", "object": "Project X",
                           "subject_id": "company-1", "relation_id": "owns-project",
                           "object_id": "project-x-1"})
        conflicting = json.dumps({"subject": "Project X", "relation": "uses database",
                                  "object": "Database Y", "subject_id": "project-x-2",
                                  "relation_id": "uses-database", "object_id": "database-y"})
        self.add(root, rid="pg-graph-id-root")
        self.add(conflicting, rid="pg-graph-id-conflict")
        self.provider.plan_value = {"intents": ["fact"], "entities": ["Orion"], "attributes": [],
                                    "obligations": [{"query": "Orion project database"}]}
        self.pipeline.config = replace(self.config, graph_depth=2, graph_retrieval_enabled=True,
                                       rerank_enabled=False)
        with patch.object(View, "dense", lambda _view, _vector, _limit: {}), \
                patch.object(View, "lexical", lambda _view, _query, _limit: {}):
            _response, trace = self.pipeline.search_with_trace(test_lifecycle.SearchRequest("Orion", "u", 5))
        self.assertEqual(trace["retrieval_channels"]["graph"], 1)
        database_node = next(node for node in self.pipeline.inspect("u")["nodes"]
                             if node["value"] == "Database Y")
        self.assertNotIn("graph_rank", trace["channel_ranks"].get(database_node["id"], {}))

    def test_rls_blocks_unscoped_and_foreign_namespace_reads_and_writes(self):
        self.add("My address is Kyoto.")
        with self.pipeline.store.transaction(namespace("other"), True) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ee_objects").fetchone()[0], 0)
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.pipeline.store.transaction(namespace("other"), True) as conn:
                conn.execute("INSERT INTO ee_namespaces(ns,generation,policy_generation) VALUES(?,0,0)", (namespace("u"),))
