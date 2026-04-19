# Memory design

## Goals

- Give Claude Code Routines a **tiny, durable** memory of what was already sent.
- Make dedupe **fast and obvious** — no ML, no fuzzy hashes — just URL and title signals.
- Guarantee **bounded growth** so the repo stays lightweight even after years of runs.

## Data files

### `state/sent_news.json`

```json
{
  "schema_version": 1,
  "updated_at": "2026-04-19T09:00:00+00:00",
  "items": [
    {
      "event_id": "4ad0c9e2fbd7c1b6",
      "title": "Anthropic launches Claude 4.7",
      "canonical_url": "https://anthropic.com/news/claude-4-7",
      "source_domain": "anthropic.com",
      "first_sent_at": "2026-04-18T08:00:00+00:00",
      "last_seen_at": "2026-04-19T08:00:00+00:00",
      "topic": "claude-ai"
    }
  ]
}
```

We deliberately **do not** store the article body, summaries, or any secondary
metadata. The only job of this file is: *"have we seen this event before?"*.

### `state/config.json`

| Key | Default | Purpose |
| --- | --- | --- |
| `retention_days` | `30` | Drop items whose `last_seen_at` is older than this. |
| `max_items` | `2000` | Hard cap on total rows; oldest evicted first. |
| `similarity_threshold` | `0.85` | Jaccard threshold on normalized title tokens. |
| `topics` | `[]` | List of canonical topic slugs used by routines. |
| `schema_version` | `1` | Bump if the item shape ever changes. |

## Event identity

An `event_id` is `sha1(canonical_url or normalized_title)[:16]`. This means:

- Two outlets reporting with the same canonical URL → same id (rare but clean).
- Two outlets with the same headline and no URL → same id via the title path.
- Different headlines covering the same story → the **title similarity** check
  catches them at dedupe time (Jaccard ≥ threshold, scoped to a single topic).

## Deduplication signals, in priority order

1. **Same `event_id`** — already known.
2. **Same `canonical_url`** — URL tracking params stripped, host lower-cased, no fragment.
3. **Near-duplicate title** — normalized tokens (lowercased, accent-stripped,
   punctuation removed, stopwords dropped) with Jaccard similarity ≥
   `similarity_threshold`, compared only within the same `topic`.

Within a single dedupe run we also compare candidates against *each other*, so
two outlets covering the same event on the same day collapse into one row.

## Growth control

Every routine run should end with `prune_state.py`, which:

1. Strips unknown fields (defends against accidental bloat).
2. Drops items with no title and no URL, or no `first_sent_at` (never actually sent).
3. Collapses duplicate `event_id` rows, keeping the freshest `last_seen_at`.
4. Expires anything older than `retention_days`.
5. Enforces `max_items` by evicting the oldest rows.

Because each row is a small flat object with a fixed set of fields, the file
size is predictable: roughly 300–400 bytes per item, so the default cap of
2000 items ≈ ~700 KB worst case.
