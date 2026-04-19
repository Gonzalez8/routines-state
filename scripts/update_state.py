#!/usr/bin/env python3
"""Record a batch of just-sent news items into the memory state.

Input: a JSON list of items that were actually included in today's digest.
Each item needs at minimum ``title`` and ``url`` (and should carry ``topic``).

Usage:
    python scripts/update_state.py --input sent_today.json
    python scripts/update_state.py --input sent_today.json --topic claude-ai

The update is idempotent: if an event is already in state, we only bump its
``last_seen_at`` instead of creating a second record.
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


def _read_sent(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("sent-today file must be a JSON list or {items: [...]}")
    return data


def merge(
    state_items: list[dict],
    sent_items: list[dict],
    default_topic: str | None = None,
) -> tuple[list[dict], int, int]:
    """Return (new_state_items, added_count, updated_count).

    We keep items keyed by ``event_id``; duplicates in the incoming batch
    collapse into a single record.
    """
    by_id: dict[str, dict] = {}
    for it in state_items:
        # Defensive: drop anything without an id and strip unknown fields.
        pruned = prune_unknown_fields(it)
        if pruned.get("event_id"):
            by_id[pruned["event_id"]] = pruned

    added = 0
    updated = 0
    now = utcnow_iso()

    for raw in sent_items:
        title = (raw.get("title") or "").strip()
        url = (raw.get("url") or raw.get("canonical_url") or "").strip()
        topic = (raw.get("topic") or default_topic or "").strip()

        if not title and not url:
            # Skip garbage rather than store a blank record.
            continue

        item = build_item(title=title, url=url, topic=topic)
        existing = by_id.get(item["event_id"])
        if existing:
            # Already known — just refresh last_seen_at and fill any gaps.
            existing["last_seen_at"] = now
            for field in REQUIRED_ITEM_FIELDS:
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]
            updated += 1
        else:
            by_id[item["event_id"]] = item
            added += 1

    # Keep output stable: newest first by first_sent_at, then by event_id.
    merged = sorted(
        by_id.values(),
        key=lambda it: (it.get("first_sent_at") or "", it.get("event_id") or ""),
        reverse=True,
    )
    return merged, added, updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record sent news items into the memory state."
    )
    parser.add_argument(
        "--input", required=True, help="Path to a JSON list of sent items."
    )
    parser.add_argument(
        "--topic",
        help="Default topic to apply to items that don't carry one.",
    )
    args = parser.parse_args()

    state = load_state()
    sent_items = _read_sent(Path(args.input))

    merged, added, updated = merge(
        state.get("items", []),
        sent_items,
        default_topic=args.topic,
    )
    state["items"] = merged
    save_state(state)

    print(
        f"update_state: added={added} updated={updated} total={len(merged)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
