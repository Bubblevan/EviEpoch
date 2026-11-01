"""Read Summary vector and lexical index coverage from the configured database."""
from __future__ import annotations

import argparse
import json

from ..config import Settings
from ..domain import namespace
from ..providers import Providers
from .repository import Repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="Namespace whose live Summary index is measured")
    args = parser.parse_args()
    config = Settings.from_env(require_postgres=False)
    store = Repository(config, Providers(config).embedding_identity())
    try:
        store.open()
        print(json.dumps(store.summary_index_metrics(namespace(args.user_id)), ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
