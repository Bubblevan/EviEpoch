"""Emit a secret-free, explicit release/research manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..domain import SCHEMA_VERSION, digest
from ..ingest.extraction import EXTRACTION_PROMPT, EXTRACTION_VERSION, PLAN_PROMPT, PLAN_VERSION
from ..retrieval.lexical import TOKENIZER_VERSION
from ..providers import Providers
from ..retrieval import RETRIEVAL_VERSION


def build(config: Settings, image_digest: str | None = None, smoke_id: str | None = None):
    root = Path(__file__).resolve().parents[2]
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    fields = asdict(config)
    for key in ("llm_api_key", "embed_api_key", "auth_token", "database_url", "db_path"):
        fields.pop(key, None)
    # Provider URLs may contain organization details; only host/path are disclosed.
    for key in ("llm_base_url", "embed_base_url", "rerank_url"):
        parts = urlsplit(fields[key])
        fields[key] = {"host": parts.hostname, "path": parts.path}
    code_hashes = {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((root / "eviepoch").rglob("*")) if p.is_file() and p.suffix in {".py", ".html"}}
    for path in [root / "requirements.txt", root / "Dockerfile", root / "THIRD_PARTY_NOTICES.md"]:
        if path.exists():
            code_hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    eligible_models = config.llm_model == "gpt-4o-mini" and config.embed_model == "text-embedding-v4"
    manifest = {"kind": "research" if config.model_policy == "research" else "release_candidate",
                "commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                "source_hashes": code_hashes, "image_digest": image_digest, "smoke_id": smoke_id,
                "schema": SCHEMA_VERSION, "configuration": fields,
                "prompts": {"extract": {"version": EXTRACTION_VERSION, "sha256": digest(EXTRACTION_PROMPT)},
                            "plan": {"version": PLAN_VERSION, "sha256": digest(PLAN_PROMPT)}},
                "tokenizer": "o200k_base; conservative UTF-8 bytes fallback", "lexical": TOKENIZER_VERSION,
                "competition_model_policy_satisfied": eligible_models and config.model_policy == "competition",
                "release_gates": {"fixed_clean_commit": False, "public_https_smoke": bool(smoke_id),
                                  "real_provider_capacity": False, "license_and_attribution": False},
                "data_retention": {"private_text_logs": False, "workbench_enabled": config.workbench_enabled,
                                   "backup_erasure": "operator-managed; not verified by this manifest"}}
    manifest["release_gates"]["fixed_clean_commit"] = not manifest["dirty"]
    manifest["manifest_hash"] = digest(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-digest")
    parser.add_argument("--smoke-id")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build(Settings.from_env(), args.image_digest, args.smoke_id),
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote secret-free manifest: {args.output}")


if __name__ == "__main__":
    main()
