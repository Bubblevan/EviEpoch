"""Start a localhost workbench on self-authored controls; no private benchmark data."""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eviepoch.api import create_app
from eviepoch.models import AddRequest, Message
from eviepoch.pipeline import MemoryPipeline
from research_config import load, make_provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--deepseek", action="store_true")
    backend.add_argument("--codex-luna", action="store_true")
    parser.add_argument("--codex-max-calls", type=int, default=32)
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--demo", action="store_true", help="seed the public address scenario")
    args = parser.parse_args()
    config = replace(load(deepseek=args.deepseek, codex_luna=args.codex_luna), workbench_enabled=True, auth_scheme="none",
                     db_path="./data/public-research.sqlite3", database_url="")
    config.validate()
    provider = make_provider(config, codex_luna=args.codex_luna, codex_max_calls=args.codex_max_calls)
    memory = MemoryPipeline(config, provider)
    if args.demo:
        memory.start()
        try:
            controls = json.loads((Path(__file__).with_name("datasets") / "public_controls.json").read_text(encoding="utf-8"))
            for entry in controls["cases"][0]["adds"]:
                memory.add(AddRequest(entry["id"], "public-demo", entry["id"],
                                     [Message(m["role"], m["content"], m.get("timestamp")) for m in entry["messages"]]))
        finally:
            memory.close()
    uvicorn.run(create_app(config, memory), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
