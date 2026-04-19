#!/usr/bin/env python3
"""Filter a candidates file against the memory state.

Reads a JSON list of candidate items (shape below), drops anything already
recorded (by canonical URL, event_id, or near-duplicate title within the
same ``(routine, topic)`` namespace), and writes the survivors to
``--output``.

Input shape (``candidates.json``)::

    [
      {"title": "...", "url": "https://...", "routine": "claude-news", "topic": "claude-ai"},
      ...
    ]

Extra fields are ignored. ``routine`` is required (either per-item or via
``--routine``). ``topic`` is optional.

Usage::

    python scripts/dedupe_candidates.py \\
        --input candidates.json \\
        --output filtered.json \\
        --routine claude-news \\
        [--topic claude-ai]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import (
    canonicalize_url,
    event_id,
    jaccard,
    load_config,
    load_state,
    normalize_title,
    title_tokens,
)


def _read_candidates(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("candidates file must be a JSON list or {items: [...]}")
    return data


def dedupe(
    candidates: list[dict],
    state_items: list[dict],
    similarity_threshold: float,
    default_routine: str | None = None,
    default_topic: str | None = None,
    routine_filter: str | None = None,
    topic_filter: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, dropped) after removing already-seen events.

    Deduplication signals, strongest first:
      1. Same ``event_id`` (namespaced by routine + topic).
      2. Same canonical URL **within the same routine**.
      3. Title Jaccard similarity ≥ threshold within the same ``(routine, topic)``.

    In-batch dedupe is applied too, so two sources covering the same event
    on the same day collapse into one row.
    """
    # Fast lookups from state.
    seen_ids: set[str] = {it["event_id"] for it in state_items if it.get("event_id")}
    # URL lookup scoped by routine so two routines may legitimately record
    # the same URL under different namespaces.
    seen_urls_by_routine: dict[str, set[str]] = {}
    seen_tokens_by_scope: dict[tuple[str, str], list[set[str]]] = {}
    for it in state_items:
        r = it.get("routine") or ""
        t = it.get("topic") or ""
        url = it.get("canonical_url") or ""
        if url:
            seen_urls_by_routine.setdefault(r, set()).add(url)
        tokens = title_tokens(it.get("title", ""))
        if tokens:
            seen_tokens_by_scope.setdefault((r, t), []).append(tokens)

    kept: list[dict] = []
    dropped: list[dict] = []
    batch_ids: set[str] = set()
    batch_urls_by_routine: dict[str, set[str]] = {}
    batch_tokens_by_scope: dict[tuple[str, str], list[set[str]]] = {}

    for raw in candidates:
        title = (raw.get("title") or "").strip()
        url = (raw.get("url") or raw.get("canonical_url") or "").strip()
        routine = (raw.get("routine") or default_routine or "").strip()
        topic = (raw.get("topic") or default_topic or "").strip()

        if routine_filter and routine != routine_filter:
            dropped.append({**raw, "_dedupe_reason": "routine-filter"})
            continue
        if topic_filter and topic != topic_filter:
            dropped.append({**raw, "_dedupe_reason": "topic-filter"})
            continue
        if not routine:
            dropped.append({**raw, "_dedupe_reason": "missing-routine"})
            continue
        if not title and not url:
            dropped.append({**raw, "_dedupe_reason": "empty"})
            continue

        canon = canonicalize_url(url)
        eid = event_id(routine, topic, title, url)
        tokens = title_tokens(title)

        reason: str | None = None
        if eid in seen_ids or eid in batch_ids:
            reason = "duplicate-event-id"
        elif canon and (
            canon in seen_urls_by_routine.get(routine, set())
            or canon in batch_urls_by_routine.get(routine, set())
        ):
            reason = "duplicate-canonical-url"
        else:
            scope = (routine, topic)
            pool: list[set[str]] = []
            pool.extend(seen_tokens_by_scope.get(scope, []))
            pool.extend(batch_tokens_by_scope.get(scope, []))
            for other in pool:
                if jaccard(tokens, other) >= similarity_threshold:
                    reason = "duplicate-similar-title"
                    break

        if reason:
            dropped.append({**raw, "_dedupe_reason": reason})
            continue

        kept.append(
            {
                "event_id": eid,
                "routine": routine,
                "topic": topic,
                "title": title,
                "url": url,
                "canonical_url": canon,
                "normalized_title": normalize_title(title),
            }
        )
        batch_ids.add(eid)
        if canon:
            batch_urls_by_routine.setdefault(routine, set()).add(canon)
        if tokens:
            batch_tokens_by_scope.setdefault((routine, topic), []).append(tokens)

    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter candidate items against the events state."
    )
    parser.add_argument("--input", required=True, help="Path to candidates JSON file.")
    parser.add_argument(
        "--output", required=True, help="Where to write the filtered JSON."
    )
    parser.add_argument(
        "--routine",
        help="Default routine for items without one; also filters the input.",
    )
    parser.add_argument(
        "--topic",
        help="Default topic for items without one; also filters the input.",
    )
    parser.add_argument(
        "--report",
        help="Optional path to write a JSON report of what was dropped and why.",
    )
    args = parser.parse_args()

    cfg = load_config()
    state = load_state()
    candidates = _read_candidates(Path(args.input))

    kept, dropped = dedupe(
        candidates,
        state.get("items", []),
        similarity_threshold=float(cfg.get("similarity_threshold", 0.85)),
        default_routine=args.routine,
        default_topic=args.topic,
        routine_filter=args.routine,
        topic_filter=args.topic,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(
                {"kept": len(kept), "dropped": dropped},
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")

    print(
        f"dedupe: kept={len(kept)} dropped={len(dropped)} "
        f"input={len(candidates)} -> {out_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
