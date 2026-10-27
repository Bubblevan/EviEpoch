"""Run the P1 fault suite on the same disposable PostgreSQL contract database."""
import unittest

import test_postgres as pg
import test_review_regressions as controls


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL URLs for P1 integration cases")
class PostgresBindingRegressions(controls.BindingRegressionTests):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)
        self.provider = controls.BindingFaults()
        self.pipeline.providers = self.provider


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL URLs for P1 integration cases")
class PostgresTemporalRegressions(controls.TemporalRegressionTests):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)


@unittest.skipUnless(pg.SERVICE and pg.ADMIN, "set disposable PostgreSQL URLs for P1 integration cases")
class PostgresLongAddRegressions(controls.LongAddRegressionTests):
    def setUp(self):
        pg.PostgresLifecycleTests.setUp(self)
        self.provider = controls.WindowProvider()
        self.pipeline.providers = self.provider
        from dataclasses import replace
        self.pipeline.config = replace(self.config, extraction_chars=2048)
