#!/usr/bin/env python3
"""Print the current memory state as JSON.

Used by a routine to inspect what is already stored. Typical usage:

    python scripts/load_state.py
    python scripts/load_state.py --routine github-monitor
    python scripts/load_state.py --routine claude-news --topic claude-ai
    python scripts/load_state.py --ids-only
"""

from __future__ import annotations

import argparse
import json
import sys

from _common import load_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and print the events state.")
    parser.add_argument(
        "--ids-only",
        action="store_true",
        help="Only print event_ids, one per line.",
    )
    parser.add_argument(
        "--routine",
        help="Filter items to a single routine before printing.",
    )
    parser.add_argument(
        "--topic",
        help="Filter items to a single topic before printing.",
    )
    args = parser.parse_args()

    state = load_state()
    items = state.get("items", [])
    if args.routine:
        items = [it for it in items if it.get("routine") == args.routine]
    if args.topic:
        items = [it for it in items if it.get("topic") == args.topic]

    if args.ids_only:
        for it in items:
            print(it.get("event_id", ""))
        return 0

    json.dump(
        {
            "schema_version": state.get("schema_version", 2),
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
