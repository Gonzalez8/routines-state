# routines-state

Tiny, file-based **persistent memory layer for Claude Code Routines**.

Claude Code Routines are stateless between runs. This repo gives them a
single, very small memory so that each run can answer one question before
doing work:

> *"Have we already processed, sent, or handled this item?"*

It is deliberately minimal:

- Pure-stdlib Python scripts (no `pip install`).
- One JSON file for state, one for config.
- Deterministic, bounded growth — safe to commit after every run.
- Reusable across **any** routine, not tied to a specific use case.

## Not an archive

This repo is **operational memory, not a historical archive**. Old items
are intentionally deleted. Records exist only as long as they are useful
for dedupe on upcoming runs. If you need long-term history, analytics, or
audit trails, put those in a separate system (a data warehouse, an
archive bucket, a proper database). Do not try to stretch this repo to
cover those needs.

## Use cases

Any routine that must remember whether it has already handled something:

- **AI news digests** — don't re-send the same story tomorrow.
- **GitHub / PR / issue monitoring** — don't re-notify about the same PR.
- **Alerts and notifications** — suppress repeated pings for the same event.
- **Repeated-content avoidance** — skip content already seen.
- Any workflow where "did we do this already?" is the decisive question.

## How a routine uses it

1. Run your search / fetch / detection step → write `candidates.json`.
2. `dedupe_candidates.py` drops anything already in `state/events.json`.
3. Generate and perform the routine action (send digest, open ticket, …).
4. `update_state.py` records what was actually handled.
5. `prune_state.py` enforces retention + cap.
6. Commit `state/events.json` so the next run inherits the memory.

## Repository layout

```
routines-state/
├── README.md
├── .gitignore
├── state/
│   ├── events.json           # the memory (the only file routines must commit)
│   └── config.json           # retention + similarity knobs
├── scripts/
│   ├── _common.py            # shared helpers (URL/title normalization, I/O)
│   ├── load_state.py         # inspect current state
│   ├── dedupe_candidates.py
│   ├── update_state.py
│   └── prune_state.py
└── docs/
    ├── memory-design.md      # data model + dedupe signals
    └── routine-integration.md
```

## Data model

Each item in `state/events.json` is a flat object with a **fixed set of
eight fields**. Nothing else is ever persisted — no summaries, no bodies,
no candidate/dedupe fields (`url`, `normalized_title`, `_dedupe_reason`,
etc.). The sanitization runs inside `save_state()` in
`scripts/_common.py`, which is the only write path to the file. Extra
fields added by hand, by a bad input, or by a future bug are stripped on
the next write.

| Field | Required | Notes |
| --- | --- | --- |
| `event_id` | yes | 16-hex stable id: `sha1("routine:topic:canonical_url_or_normalized_title")[:16]`. |
| `routine` | yes | Primary namespace. Distinguishes different routines sharing this store. |
| `topic` | optional | Secondary grouping within a routine (e.g. `"claude-ai"`, a repo name). May be empty. |
| `title` | one of title/url required | Human-readable headline or identifier. |
| `canonical_url` | one of title/url required | URL with tracking params stripped, host lower-cased, no fragment. |
| `source_domain` | derived | Convenience field — the host of `canonical_url`. |
| `first_sent_at` | yes | ISO-8601 UTC when we first recorded the item. |
| `last_seen_at` | yes | ISO-8601 UTC when we most recently observed the item. |

### Event identity is namespaced

`event_id` is derived from `routine + topic + (canonical_url or normalized_title)`.
That prevents collisions when two unrelated routines happen to process
similar-looking items. **`routine` is the primary namespace**; `topic` is
a convenience for grouping within a routine and can be empty.

## Deduplication

Three signals, checked in order. First match wins.

1. **Same `event_id`** — already known (namespaced, so safe across routines).
2. **Same canonical URL**, scoped to the same `routine`.
3. **Near-duplicate title** — Jaccard ≥ `similarity_threshold` over
   normalized, stop-worded, accent-stripped tokens. Scoped to the same
   `(routine, topic)` pair to prevent cross-context false positives.

In-batch dedupe is applied too — two sources covering the same event on
the same day collapse into one row.

See [docs/memory-design.md](docs/memory-design.md) for details.

## Growth control (bounded by construction)

`prune_state.py` enforces the retention policy:

- Keep only items whose `last_seen_at` falls inside `retention_days`
  (default **30**).
- Keep only **one** record per `event_id` (freshest wins).
- Drop items with no `routine`, no title+URL, or no `first_sent_at`.
- Enforce `max_items` (default **2000**) by evicting oldest rows first.
- Strip unknown fields before persisting — no accidental bloat.

Fixed-shape rows (~300–500 bytes each) × default cap (2000) ≈ under ~1 MB
worst-case on disk.

## Configuration (`state/config.json`)

```json
{
  "retention_days": 30,
  "max_items": 2000,
  "similarity_threshold": 0.85,
  "routines": [],
  "topics": [],
  "schema_version": 2
}
```

| Key | Default | Purpose |
| --- | --- | --- |
| `retention_days` | `30` | Rolling dedupe window. |
| `max_items` | `2000` | Hard cap regardless of time window. |
| `similarity_threshold` | `0.85` | Title Jaccard cutoff (0–1). |
| `routines` | `[]` | Optional list of known routine slugs (documentation only). |
| `topics` | `[]` | Optional list of known topic slugs (documentation only). |
| `schema_version` | `2` | Bump if the item shape ever changes. |

## Scripts: CLI reference

Python 3.9+, stdlib only.

### `scripts/load_state.py`

```bash
python scripts/load_state.py                                 # full state
python scripts/load_state.py --ids-only                      # event_ids only
python scripts/load_state.py --routine claude-news           # scope to one routine
python scripts/load_state.py --routine claude-news --topic claude-ai
```

### `scripts/dedupe_candidates.py`

```bash
python scripts/dedupe_candidates.py \
    --input candidates.json \
    --output filtered.json \
    --routine claude-news \
    [--topic claude-ai] \
    [--report dedupe_report.json]
```

Input accepts either a bare list or `{"items": [...]}`:

```json
[
  { "title": "Anthropic launches Claude 4.7", "url": "https://...", "routine": "claude-news", "topic": "claude-ai" }
]
```

`--routine` is used both as a filter and as the default for items that
don't carry a `routine` field. `--topic` behaves the same way. Extra fields
in the input are ignored.

### `scripts/update_state.py`

```bash
python scripts/update_state.py --input processed_today.json --routine claude-news
```

Idempotent. Items missing both `title` and `url`, or missing `routine`
(after applying the CLI default), are skipped.

### `scripts/prune_state.py`

```bash
python scripts/prune_state.py                              # all routines
python scripts/prune_state.py --dry-run                    # preview
python scripts/prune_state.py --retention-days 14 --max-items 500
python scripts/prune_state.py --routine claude-news        # scope to one routine
```

When `--routine` is passed, items from other routines are left untouched.

## Multi-routine examples

### 1. AI news digest

```bash
# Candidates written by the routine's search step.
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json \
    --routine claude-news --topic claude-ai

# Send the digest from filtered.json; record what went out:
python scripts/update_state.py --input sent_today.json --routine claude-news --topic claude-ai
python scripts/prune_state.py --routine claude-news
```

### 2. GitHub PR / issue monitor

```bash
# candidates.json: [{"title": "...", "url": "https://github.com/org/repo/pull/123",
#                    "routine": "gh-monitor", "topic": "org/repo"}, ...]
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json \
    --routine gh-monitor --topic org/repo

python scripts/update_state.py --input notified.json --routine gh-monitor --topic org/repo
python scripts/prune_state.py --routine gh-monitor
```

### 3. Salesforce digest

```bash
# 1. Your routine queries Salesforce and writes candidates.json:
#    [{"title": "Opportunity XYZ moved to Closed-Won", "url": "https://acme.my.salesforce.com/006.../view",
#      "routine": "salesforce-digest", "topic": "opportunities"}, ...]

# 2. Filter out anything already handled.
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json \
    --routine salesforce-digest --topic opportunities

# 3. Generate and send the digest from filtered.json.

# 4. Your routine writes sent_today.json with exactly the items that were
#    actually included in the digest (same shape as candidates.json).

# 5. Record what was sent — only the 8 canonical fields land in state/events.json.
python scripts/update_state.py --input sent_today.json \
    --routine salesforce-digest --topic opportunities

# 6. Keep state bounded.
python scripts/prune_state.py --routine salesforce-digest

# 7. Commit.
git add state/events.json
git commit -m "chore(state): $(date -u +%Y-%m-%d) salesforce-digest"
git push
```

### 4. Generic alerts / notifications

```bash
# An alert system that must not page twice for the same incident URL.
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json \
    --routine oncall-alerts

python scripts/update_state.py --input delivered.json --routine oncall-alerts
python scripts/prune_state.py --routine oncall-alerts
```

All three routines share the same `state/events.json` safely — their
`event_id`s are namespaced by `routine`, so they cannot collide.

## Daily flow (copy-paste template)

```bash
# 1. Your routine produces candidates.json (shape: [{title, url, routine, topic}, ...]).

# 2. Filter.
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json --routine <slug>

# 3. Do the work (send digest, open ticket, notify, ...) using filtered.json.
#    Write what was actually handled to handled.json.

# 4. Record and prune.
python scripts/update_state.py --input handled.json --routine <slug>
python scripts/prune_state.py

# 5. Commit.
git add state/events.json
git commit -m "chore(state): $(date -u +%Y-%m-%d) <slug>"
git push
```

## Design principles

- **One job.** Remember which events were handled. Nothing else.
- **Operational memory, not archive.** Old rows are deleted on purpose.
- **Stdlib only.** No package manager needed in a routine.
- **Atomic writes.** Temp file + `os.replace`, so a crash can't corrupt state.
- **Bounded by construction.** Fixed-shape rows + retention + hard cap.
- **Namespaced identity.** `routine` is the primary key; different routines
  cannot collide even if their items look similar.

## License

MIT — do whatever is useful.
