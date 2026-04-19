#!/usr/bin/env python3
"""Record a batch of just-processed items into the memory state.

Input: a JSON list of items that were actually handled by the routine this
run (sent, notified, processed — whatever "handled" means for you). Each
item needs at minimum ``title`` or ``url``, and must resolve to a
``routine`` either per-item or via ``--routine``.

Usage::

    python scripts/update_state.py --input processed_today.json --routine claude-news
    python scripts/update_state.py --input processed_today.json --routine gh-monitor --topic anthropics/anthropic-cookbook

The update is idempotent: if an event is already in state, only its
``last_seen_at`` is refreshed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import (
    REQUIRED_ITEM_FIELDS,
    build_item,
    load_state,
    prune_unknown_fields,
    save_state,
    utcnow_iso,
)


def _read_processed(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("input file must be a JSON list or {items: [...]}")
    return data


def merge(
    state_items: list[dict],
    processed_items: list[dict],
    default_routine: str | None = None,
    default_topic: str | None = None,
) -> tuple[list[dict], int, int, int]:
    """Return (merged, added, updated, skipped)."""
    by_id: dict[str, dict] = {}
    for it in state_items:
        pruned = prune_unknown_fields(it)
        if pruned.get("event_id"):
            by_id[pruned["event_id"]] = pruned

    added = 0
    updated = 0
    skipped = 0
    now = utcnow_iso()

    for raw in processed_items:
        title = (raw.get("title") or "").strip()
        url = (raw.get("url") or raw.get("canonical_url") or "").strip()
        routine = (raw.get("routine") or default_routine or "").strip()
        topic = (raw.get("topic") or default_topic or "").strip()

        # Skip invalid rows rather than persist garbage.
        if not routine or (not title and not url):
            skipped += 1
            continue

        # build_item already returns only the 8 canonical fields; pass it
        # through prune_unknown_fields anyway so the contract is explicit.
        item = prune_unknown_fields(
            build_item(routine=routine, topic=topic, title=title, url=url)
        )
        existing = by_id.get(item["event_id"])
        if existing:
            existing["last_seen_at"] = now
            for field in REQUIRED_ITEM_FIELDS:
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]
            updated += 1
        else:
            by_id[item["event_id"]] = item
            added += 1

    merged = sorted(
        by_id.values(),
        key=lambda it: (it.get("first_sent_at") or "", it.get("event_id") or ""),
        reverse=True,
    )
    return merged, added, updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record processed items into the events memory."
    )
    parser.add_argument(
        "--input", required=True, help="Path to a JSON list of processed items."
    )
    parser.add_argument(
        "--routine",
        help="Default routine to apply to items that don't carry one.",
    )
    parser.add_argument(
        "--topic",
        help="Default topic to apply to items that don't carry one.",
    )
    args = parser.parse_args()

    state = load_state()
    processed = _read_processed(Path(args.input))

    merged, added, updated, skipped = merge(
        state.get("items", []),
        processed,
        default_routine=args.routine,
        default_topic=args.topic,
    )
    state["items"] = merged
    save_state(state)

    print(
        f"update_state: added={added} updated={updated} skipped={skipped} total={len(merged)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
