#!/usr/bin/env python3
"""Print the current memory state as JSON.

Used by a routine to inspect what has already been sent. Typical usage:

    python scripts/load_state.py                # full state
    python scripts/load_state.py --ids-only     # just event ids
    python scripts/load_state.py --topic claude-ai
"""

from __future__ import annotations

import argparse
import json
import sys

from _common import load_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and print the sent-news state.")
    parser.add_argument(
        "--ids-only",
        action="store_true",
        help="Only print the list of event_ids (one per line).",
    )
    parser.add_argument(
        "--topic",
        help="Filter items to a single topic before printing.",
    )
    args = parser.parse_args()

    state = load_state()
    items = state.get("items", [])
    if args.topic:
        items = [it for it in items if it.get("topic") == args.topic]

    if args.ids_only:
        for it in items:
            print(it.get("event_id", ""))
        return 0

    json.dump(
        {
            "schema_version": state.get("schema_version", 1),
            "updated_at": state.get("updated_at"),
            "count": len(items),
            "items": items,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
