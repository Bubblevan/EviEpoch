"""Run semantic-packing controls against the disposable PostgreSQL backend."""
import unittest
import test_postgres as pg
import test_smoke_semantics as controls


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL test URLs")
class PostgresPackingControls(controls.PackingControls):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)
