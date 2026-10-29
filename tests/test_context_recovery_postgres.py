import unittest
import test_postgres as pg
import test_context_recovery as controls


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL test URLs")
class PostgresContextRecovery(controls.ContextRecoveryControls):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)
