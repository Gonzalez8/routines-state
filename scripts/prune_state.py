#!/usr/bin/env python3
"""Trim the memory state so it never grows without bound.

This repo is **operational memory**, not an archive. Pruning is mandatory,
not optional.

Retention policy (all configurable via ``state/config.json``):

  * Drop items older than ``retention_days`` (based on ``last_seen_at``,
    falling back to ``first_sent_at``).
  * Drop items that look blank or were never actually processed (no
    ``routine``, or no title+URL, or no ``first_sent_at`` timestamp).
  * Collapse duplicate ``event_id`` records, keeping the most recent.
  * Enforce ``max_items`` by evicting the oldest rows first.
  * Optionally scope the run to a single routine via ``--routine``.

Usage::

    python scripts/prune_state.py                              # full state
    python scripts/prune_state.py --dry-run                    # preview
    python scripts/prune_state.py --retention-days 14          # override
    python scripts/prune_state.py --routine claude-news        # scope
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from _common import (
    REQUIRED_ITEM_FIELDS,
    load_config,
    load_state,
    prune_unknown_fields,
    save_state,
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _item_timestamp(item: dict) -> datetime | None:
    return _parse_ts(item.get("last_seen_at")) or _parse_ts(item.get("first_sent_at"))


def prune(
    items: list[dict],
    retention_days: int,
    max_items: int,
    routine: str | None = None,
) -> tuple[list[dict], dict]:
    """Apply the retention policy. Returns (kept_items, stats).

    When ``routine`` is set, only items in that routine are evaluated;
    items belonging to other routines are preserved untouched.
    """
    stats = {
        "input": len(items),
        "dropped_blank": 0,
        "dropped_expired": 0,
        "dropped_duplicate": 0,
        "dropped_over_cap": 0,
    }

    untouched: list[dict] = []
    scoped: list[dict] = []
    for raw in items:
        if routine is not None and (raw.get("routine") or "") != routine:
            untouched.append(raw)
        else:
            scoped.append(raw)

    # 1) Strip unknown fields + drop invalid rows.
    cleaned: list[dict] = []
    for raw in scoped:
        item = prune_unknown_fields(raw)
        has_content = bool((item.get("title") or "").strip() or item.get("canonical_url"))
        if not item.get("routine") or not has_content or not item.get("first_sent_at"):
            stats["dropped_blank"] += 1
            continue
        cleaned.append(item)

    # 2) Collapse duplicate event_ids, keeping the freshest record.
    by_id: dict[str, dict] = {}
    for item in cleaned:
        eid = item.get("event_id")
        if not eid:
            stats["dropped_blank"] += 1
            continue
        existing = by_id.get(eid)
        if existing is None:
            by_id[eid] = item
            continue
        keep_new = (_item_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc)) >= (
            _item_timestamp(existing) or datetime.min.replace(tzinfo=timezone.utc)
        )
        if keep_new:
            by_id[eid] = item
        stats["dropped_duplicate"] += 1

    deduped = list(by_id.values())

    # 3) Expire anything older than the retention window.
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, retention_days))
    fresh: list[dict] = []
    for item in deduped:
        ts = _item_timestamp(item)
        if ts is None or ts < cutoff:
            stats["dropped_expired"] += 1
            continue
        fresh.append(item)

    # 4) Enforce the absolute cap. Oldest go first.
    fresh.sort(
        key=lambda it: _item_timestamp(it) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if max_items > 0 and len(fresh) > max_items:
        stats["dropped_over_cap"] = len(fresh) - max_items
        fresh = fresh[:max_items]

    fresh = [{k: it.get(k) for k in REQUIRED_ITEM_FIELDS} for it in fresh]

    # Put the untouched (other-routine) items back.
    kept = untouched + fresh
    stats["output"] = len(kept)
    return kept, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old/duplicate items from state.")
    parser.add_argument(
        "--retention-days", type=int, help="Override config.retention_days."
    )
    parser.add_argument(
        "--max-items", type=int, help="Override config.max_items."
    )
    parser.add_argument(
        "--routine",
        help="Only prune items from this routine. Others are left untouched.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute what would change but don't write the state file.",
    )
    args = parser.parse_args()

    cfg = load_config()
    retention_days = (
        args.retention_days if args.retention_days is not None else int(cfg["retention_days"])
    )
    max_items = args.max_items if args.max_items is not None else int(cfg["max_items"])

    state = load_state()
    kept, stats = prune(
        state.get("items", []),
        retention_days=retention_days,
        max_items=max_items,
        routine=args.routine,
    )

    print(
        "prune: "
        f"input={stats['input']} output={stats['output']} "
        f"blank={stats['dropped_blank']} duplicate={stats['dropped_duplicate']} "
        f"expired={stats['dropped_expired']} over_cap={stats['dropped_over_cap']}"
        + (f" routine={args.routine}" if args.routine else ""),
        file=sys.stderr,
    )

    if args.dry_run:
        return 0

    state["items"] = kept
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
