"""The new synthetic lifecycle controls on a disposable PostgreSQL database."""
import unittest
import test_postgres as pg
import test_memory_improvements as controls


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL URLs for integration cases")
class PostgresImprovementControls(controls.ImprovementControls):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)
