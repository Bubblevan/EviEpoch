from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def send(base_url: str, operation: str, payload: dict, headers: dict[str, str]) -> dict:
    request = Request(
        f"{base_url.rstrip('/')}/{operation}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"line request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach service: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Add/Search JSONL against a running EviEpoch service")
    parser.add_argument("jsonl", help="JSONL file with one {op, payload} object per line")
    parser.add_argument("--url", default="http://127.0.0.1:8012", help="service base URL")
    parser.add_argument("--token", default=os.getenv("EPOCH_AUTH_TOKEN", ""), help="API token")
    parser.add_argument("--auth-scheme", default=os.getenv("EPOCH_AUTH_SCHEME", "bearer"),
                        choices=("none", "bearer", "token", "x-api-key"))
    args = parser.parse_args()

    headers: dict[str, str] = {}
    if args.auth_scheme == "x-api-key" and args.token:
        headers["X-API-Key"] = args.token
    elif args.auth_scheme in {"bearer", "token"} and args.token:
        headers["Authorization"] = f"{args.auth_scheme.title()} {args.token}"

    added = searches = relevant = found = complete = scored = 0
    try:
        with open(args.jsonl, "r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                operation = row.get("op")
                if operation not in {"add", "search"} or not isinstance(row.get("payload"), dict):
                    raise ValueError(f"line {line_number}: expected op add/search and an object payload")
                response = send(args.url, operation, row["payload"], headers)
                if operation == "add":
                    added += 1
                    continue
                searches += 1
                actual = {item["id"] for item in response.get("data", [])}
                gold = set(row.get("gold_ids", []))
                if gold:
                    scored += 1
                    relevant += len(gold)
                    found += len(gold & actual)
                    complete += int(gold <= actual)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = {
        "adds": added,
        "searches": searches,
        "scored_searches": scored,
        "evidence_recall": found / relevant if relevant else None,
        "complete_evidence_rate": complete / scored if scored else None,
        "relevant_ids": relevant,
        "found_ids": found,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
