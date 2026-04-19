#!/usr/bin/env python3
"""Filter a candidates file against the memory state.

Reads a JSON list of candidate news items (shape below), drops anything
already sent (by canonical URL, by event_id, or by near-duplicate title),
and writes the remaining items to ``--output``.

Input shape (``candidates.json``)::

    [
      {"title": "...", "url": "https://...", "topic": "claude-ai"},
      ...
    ]

Extra fields in the input are ignored — only title/url/topic matter here.

Usage:
    python scripts/dedupe_candidates.py \\
        --input candidates.json \\
        --output filtered.json \\
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
    # Accept either a bare list or ``{"items": [...]}`` for convenience.
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("candidates file must be a JSON list or {items: [...]}")
    return data


def dedupe(
    candidates: list[dict],
    state_items: list[dict],
    similarity_threshold: float,
    topic_filter: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, dropped) after removing already-seen events.

    Deduplication signals, in order of strength:
      1. Same event_id (hash of canonical URL or normalized title).
      2. Same canonical URL.
      3. Title Jaccard similarity >= threshold within the same topic.

    We also dedupe *within* the candidates list itself so a digest never
    includes two variants of the same story from different outlets.
    """
    # Build fast lookups from state.
    seen_ids: set[str] = {it["event_id"] for it in state_items if it.get("event_id")}
    seen_urls: set[str] = {
        it["canonical_url"] for it in state_items if it.get("canonical_url")
    }
    # Token sets grouped by topic — cheap enough at this scale.
    seen_tokens_by_topic: dict[str, list[set[str]]] = {}
    for it in state_items:
        topic = it.get("topic") or ""
        tokens = title_tokens(it.get("title", ""))
        if tokens:
            seen_tokens_by_topic.setdefault(topic, []).append(tokens)

    kept: list[dict] = []
    dropped: list[dict] = []
    # Track what we've already admitted this run, so in-batch dupes also drop.
    batch_ids: set[str] = set()
    batch_urls: set[str] = set()
    batch_tokens_by_topic: dict[str, list[set[str]]] = {}

    for raw in candidates:
        title = (raw.get("title") or "").strip()
        url = (raw.get("url") or "").strip()
        topic = (raw.get("topic") or "").strip()

        if topic_filter and topic != topic_filter:
            dropped.append({**raw, "_dedupe_reason": "topic-filter"})
            continue

        if not title and not url:
            dropped.append({**raw, "_dedupe_reason": "empty"})
            continue

        canon = canonicalize_url(url)
        eid = event_id(title, url)
        tokens = title_tokens(title)

        reason: str | None = None
        if eid in seen_ids or eid in batch_ids:
            reason = "duplicate-event-id"
        elif canon and (canon in seen_urls or canon in batch_urls):
            reason = "duplicate-canonical-url"
        else:
            # Title similarity check, scoped to the same topic to avoid
            # cross-topic false positives.
            pool: list[set[str]] = []
            pool.extend(seen_tokens_by_topic.get(topic, []))
            pool.extend(batch_tokens_by_topic.get(topic, []))
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
                "title": title,
                "url": url,
                "canonical_url": canon,
                "topic": topic,
                "normalized_title": normalize_title(title),
            }
        )
        batch_ids.add(eid)
        if canon:
            batch_urls.add(canon)
        if tokens:
            batch_tokens_by_topic.setdefault(topic, []).append(tokens)

    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter candidate news against the sent-news state."
    )
    parser.add_argument("--input", required=True, help="Path to candidates JSON file.")
    parser.add_argument(
        "--output", required=True, help="Where to write the filtered JSON."
    )
    parser.add_argument(
        "--topic",
        help="Only keep candidates matching this topic (also used for scoping similarity).",
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
