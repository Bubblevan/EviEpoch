from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from eviepoch.config import Settings


class DatabaseConfigurationTests(unittest.TestCase):
    @patch.dict(os.environ, {"EPOCH_DATABASE_URL": "", "EPOCH_ALLOW_SQLITE": ""}, clear=False)
    def test_production_config_requires_postgres_even_when_sqlite_flag_is_blank(self):
        with self.assertRaisesRegex(ValueError, "EPOCH_DATABASE_URL is required"):
            Settings.from_env()

    @patch.dict(os.environ, {"EPOCH_DATABASE_URL": "", "EPOCH_ALLOW_SQLITE": "1"}, clear=False)
    def test_sqlite_requires_explicit_opt_in(self):
        config = Settings.from_env()
        self.assertTrue(config.allow_sqlite)
        self.assertEqual(config.database_url, "")

    @patch.dict(os.environ, {"EPOCH_DATABASE_URL": "postgresql://example/db", "EPOCH_GRAPH_RETRIEVAL_ENABLED": ""}, clear=False)
    def test_entity_graph_retrieval_is_enabled_by_default_and_can_be_disabled(self):
        self.assertTrue(Settings.from_env().graph_retrieval_enabled)
        with patch.dict(os.environ, {"EPOCH_GRAPH_RETRIEVAL_ENABLED": "0"}, clear=False):
            self.assertFalse(Settings.from_env().graph_retrieval_enabled)


if __name__ == "__main__":
    unittest.main()
