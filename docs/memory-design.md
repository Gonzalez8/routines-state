# Memory design

## Goals

- Give Claude Code Routines a **tiny, durable** operational memory of what
  has already been handled.
- Work for **any** routine — news digests, GitHub monitors, alerts,
  notifications, content-dedupe loops.
- Make dedupe **fast and obvious** — no ML, no fuzzy hashes — just URL and
  title signals, namespaced by routine.
- Guarantee **bounded growth** so the repo stays lightweight forever.

## Not an archive

This is **operational memory**, not a historical record. Old items are
intentionally expired. If you need history, analytics, or audit trails,
put them in a separate system — this repo is not the right place.

## Data files

### `state/events.json`

```json
{
  "schema_version": 2,
  "updated_at": "2026-04-19T09:00:00+00:00",
  "items": [
    {
      "event_id": "4ad0c9e2fbd7c1b6",
      "routine": "claude-news",
      "topic": "claude-ai",
      "title": "Anthropic launches Claude 4.7",
      "canonical_url": "https://anthropic.com/news/claude-4-7",
      "source_domain": "anthropic.com",
      "first_sent_at": "2026-04-18T08:00:00+00:00",
      "last_seen_at": "2026-04-19T08:00:00+00:00"
    }
  ]
}
```

Fields:

| Field | Required | Role |
| --- | --- | --- |
| `event_id` | yes | Stable, namespaced id (see below). |
| `routine` | yes | Primary namespace — distinguishes different routines sharing this store. |
| `topic` | optional | Secondary grouping within a routine. May be empty. |
| `title` | one-of | Human-readable label. |
| `canonical_url` | one-of | URL with tracking params removed, host lower-cased, no fragment. |
| `source_domain` | derived | Host of `canonical_url`. |
| `first_sent_at` | yes | When we first recorded this event. |
| `last_seen_at` | yes | When we most recently observed it. |

"One-of" means at least one of `title` / `canonical_url` must be present.
Items missing both are rejected on write and removed on prune.

We deliberately **do not** store article bodies, summaries, payloads, or
any secondary metadata. The only job of this file is answering: *have we
handled this before?*

### Only the 8 fields above are ever persisted

Intermediate fields that appear in transient files (`url`,
`normalized_title`, `_dedupe_reason`, …) never reach `events.json`.
Enforcement happens at the single write chokepoint — `save_state()` in
`scripts/_common.py` — which runs every item through
`prune_unknown_fields` before the atomic write. This is belt-and-braces:
`build_item()` already returns only the canonical fields, and
`update_state.py` sanitizes again before merging. The net effect is that
no matter what garbage the caller passes in, the file on disk always
matches the schema exactly.

### `state/config.json`

| Key | Default | Purpose |
| --- | --- | --- |
| `retention_days` | `30` | Drop items whose `last_seen_at` is older than this. |
| `max_items` | `2000` | Hard cap on total rows; oldest evicted first. |
| `similarity_threshold` | `0.85` | Jaccard threshold on normalized title tokens. |
| `routines` | `[]` | Optional list of canonical routine slugs (documentation aid). |
| `topics` | `[]` | Optional list of canonical topic slugs (documentation aid). |
| `schema_version` | `2` | Bump if the item shape ever changes. |

## Event identity

`event_id = sha1(f"{routine}:{topic}:{canonical_url or normalized_title}")[:16]`

Namespacing by **routine** (and optionally **topic**) prevents collisions
across unrelated workflows. Two routines that legitimately process the
same URL will produce *different* `event_id`s — they can share the store
without interference.

- Same `canonical_url` in the same routine → same id (strong signal).
- Same headline with no URL, same routine → same id via the title path.
- Different headlines about the same story → caught by the **title
  similarity** check at dedupe time (Jaccard ≥ threshold, scoped to
  `(routine, topic)`).

## Deduplication signals, strongest first

1. **Same `event_id`** — already known. Safe across routines because ids
   are namespaced.
2. **Same `canonical_url` within the same routine** — tracking params
   stripped, host lower-cased, fragment dropped, trailing slash trimmed.
3. **Near-duplicate title** — tokens lowercased, accent-stripped,
   punctuation removed, stopwords dropped; Jaccard similarity ≥
   `similarity_threshold`, compared only within the same
   `(routine, topic)`.

`dedupe_candidates.py` also compares candidates against *each other* in
the same run, so two sources covering the same event on the same day
collapse into one row.

## Growth control

Every routine run should end with `prune_state.py`. It:

1. Strips unknown fields — defends against accidental bloat.
2. Drops rows missing `routine`, or with no title+URL, or with no
   `first_sent_at` (never actually handled).
3. Collapses duplicate `event_id` rows, keeping the freshest
   `last_seen_at`.
4. Expires anything older than `retention_days`.
5. Enforces `max_items` by evicting oldest rows.

Each row is a flat object with a fixed set of fields, so the file size is
predictable: roughly 300–500 bytes per item, so the default cap of 2000
items translates to under ~1 MB on disk.

## Concurrency

Writes are atomic (temp file + `os.replace`). If two routines try to
update the state at the same time, the loser simply overwrites with its
own view of the merge — no corruption, but you can lose an update. In
practice, routines are scheduled at different minutes, so this is a
non-issue. If it ever becomes one, serialize by committing through git.
