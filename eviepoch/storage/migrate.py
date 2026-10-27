"""Explicit PostgreSQL bootstrap. Run with a separate schema-admin URL."""
from __future__ import annotations

import argparse
import json
import os

from ..config import Settings
from ..providers import Providers
from .repository import schema

TABLES = ("ee_namespaces", "ee_requests", "ee_objects", "ee_vectors", "ee_postings",
          "ee_dependencies", "ee_provenance", "ee_versions", "ee_edges", "ee_tombstones", "ee_operations")


def migrate(admin_url: str, service_role: str, config: Settings):
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(schema(True, config.embed_dimensions))
        identity = json.dumps(Providers(config).embedding_identity(), sort_keys=True)
        row = conn.execute("SELECT value FROM ee_metadata WHERE key='embedding_identity'").fetchone()
        if row and row[0] != identity:
            raise RuntimeError("Embedding identity changed; create and reimport into a new database")
        conn.execute("INSERT INTO ee_metadata(key,value) VALUES('embedding_identity',%s) ON CONFLICT DO NOTHING", (identity,))
        conn.execute("INSERT INTO ee_metadata(key,value) VALUES('schema_version','1') ON CONFLICT DO NOTHING")
        role = sql.Identifier(service_role)
        for table in TABLES:
            name = sql.Identifier(table)
            conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(name))
            conn.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(name))
            conn.execute(sql.SQL("DROP POLICY IF EXISTS namespace_isolation ON {}").format(name))
            conn.execute(sql.SQL("CREATE POLICY namespace_isolation ON {} USING "
                                 "(ns=current_setting('eviepoch.namespace',true)) WITH CHECK "
                                 "(ns=current_setting('eviepoch.namespace',true))").format(name))
            conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {} TO {}").format(name, role))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT ON ee_metadata TO {}").format(role))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="eviepoch_app")
    args = parser.parse_args()
    admin = os.getenv("EPOCH_ADMIN_DATABASE_URL", "")
    if not admin:
        parser.error("Set EPOCH_ADMIN_DATABASE_URL for migration; never use it as the service URL")
    migrate(admin, args.role, Settings.from_env())
    print("schema v1 ready; service role has namespace RLS")


if __name__ == "__main__":
    main()
