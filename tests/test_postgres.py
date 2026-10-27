"""Opt-in integration suite; requires a disposable database ending in _test."""
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from eviepoch.config import Settings
from eviepoch.domain import namespace
from eviepoch.pipeline import MemoryPipeline
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

    def test_rls_blocks_unscoped_and_foreign_namespace_reads_and_writes(self):
        self.add("My address is Kyoto.")
        with self.pipeline.store.transaction(namespace("other"), True) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ee_objects").fetchone()[0], 0)
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.pipeline.store.transaction(namespace("other"), True) as conn:
                conn.execute("INSERT INTO ee_namespaces(ns,generation,policy_generation) VALUES(?,0,0)", (namespace("u"),))
