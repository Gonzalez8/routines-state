# routines-state

Tiny, file-based memory for **Claude Code Routines** that run daily news
digests. The repo stores the minimum amount of state required to answer one
question each morning:

> *"Have we already sent this news item?"*

It is intentionally small:

- Pure-stdlib Python scripts (no `pip install` step).
- One JSON file for state, one JSON file for config.
- Deterministic, bounded growth — safe to commit on every run.

## Why it exists

Claude Code Routines are stateless between runs. If a routine searches for
"Claude AI / Anthropic" news every morning, it will happily surface the same
headline three days in a row unless it has a memory of what it already sent.
This repo is that memory.

The routine's daily loop becomes:

1. Search for recent news → `candidates.json`.
2. `dedupe_candidates.py` removes anything already in `state/sent_news.json`.
3. Generate and send the digest from the filtered list.
4. `update_state.py` records what actually went out.
5. `prune_state.py` enforces retention + size caps.
6. Commit `state/sent_news.json` so the next run inherits the memory.

## Repository layout

```
routines-state/
├── README.md
├── .gitignore
├── state/
│   ├── sent_news.json      # the memory (only file routines need to commit)
│   └── config.json         # retention + similarity knobs
├── scripts/
│   ├── _common.py          # shared helpers (URL/title normalization, I/O)
│   ├── load_state.py       # inspect current state
│   ├── dedupe_candidates.py
│   ├── update_state.py
│   └── prune_state.py
└── docs/
    ├── memory-design.md    # data model + dedupe signals
    └── routine-integration.md
```

## How deduplication works

Three signals, checked in order. The first match wins.

1. **Same `event_id`** — `sha1(canonical_url or normalized_title)[:16]`.
2. **Same canonical URL** — tracking params stripped, host lower-cased,
   fragment removed, trailing slash trimmed.
3. **Near-duplicate title** — Jaccard similarity ≥ `similarity_threshold`
   (default `0.85`) over normalized, stop-worded, accent-stripped tokens,
   scoped to a single `topic`.

Dedupe also collapses in-batch duplicates, so a digest never includes two
outlets' write-ups of the same event on the same day.

See [docs/memory-design.md](docs/memory-design.md) for the full data model.

## How pruning works

`prune_state.py` applies a strict retention policy:

- Keep only items whose `last_seen_at` falls inside the last
  `retention_days` (default **30**).
- Keep only **one** record per `event_id` (freshest `last_seen_at` wins).
- Drop items that were never actually sent (missing `first_sent_at`).
- Enforce `max_items` (default **2000**) by evicting oldest rows first.
- Strip unknown fields before persisting (no accidental bloat).

Worst-case disk footprint is small and predictable: ~300–400 bytes per row,
so the default cap translates to under ~1 MB on disk.

Run it in dry-run mode to preview:

```bash
python scripts/prune_state.py --dry-run
```

## Configuration

`state/config.json`:

```json
{
  "retention_days": 30,
  "max_items": 2000,
  "similarity_threshold": 0.85,
  "topics": ["claude-ai", "anthropic"],
  "schema_version": 1
}
```

| Key | Default | Notes |
| --- | --- | --- |
| `retention_days` | `30` | Rolling window for dedupe memory. |
| `max_items` | `2000` | Hard cap regardless of time window. |
| `similarity_threshold` | `0.85` | Title-similarity cutoff (Jaccard, 0–1). |
| `topics` | `[]` | Canonical topic slugs your routines pass via `--topic`. |
| `schema_version` | `1` | Bump if the item shape ever changes. |

## Scripts: CLI reference

All scripts use Python 3.9+ and no third-party dependencies.

### `scripts/load_state.py`

Print the current memory as JSON.

```bash
python scripts/load_state.py                    # full state
python scripts/load_state.py --ids-only         # just event_ids, one per line
python scripts/load_state.py --topic claude-ai  # filter by topic
```

### `scripts/dedupe_candidates.py`

Filter a candidates file against state.

```bash
python scripts/dedupe_candidates.py \
    --input candidates.json \
    --output filtered.json \
    --topic claude-ai \
    --report dedupe_report.json   # optional audit trail
```

Input shape (either a bare list or `{"items": [...]}`):

```json
[
  { "title": "Anthropic launches Claude 4.7", "url": "https://...", "topic": "claude-ai" }
]
```

Extra fields are ignored.

### `scripts/update_state.py`

Record items that were actually sent.

```bash
python scripts/update_state.py --input sent_today.json --topic claude-ai
```

Idempotent: re-running with the same input only bumps `last_seen_at` on
already-recorded events.

### `scripts/prune_state.py`

Apply retention + cap.

```bash
python scripts/prune_state.py
python scripts/prune_state.py --dry-run
python scripts/prune_state.py --retention-days 14 --max-items 500
```

## Example workflow (before and after sending the digest)

```bash
# --- BEFORE ---
# Your routine writes candidates.json with the news it found.
python scripts/dedupe_candidates.py \
    --input candidates.json \
    --output filtered.json \
    --topic claude-ai

# Your routine uses filtered.json to build and send the digest,
# writing the items that actually went out into sent_today.json.

# --- AFTER ---
python scripts/update_state.py --input sent_today.json --topic claude-ai
python scripts/prune_state.py

git add state/sent_news.json
git commit -m "chore(state): daily update $(date -u +%Y-%m-%d)"
git push
```

More detail in [docs/routine-integration.md](docs/routine-integration.md).

## Design principles

- **One job.** Remember which events were sent. Nothing else.
- **Stdlib only.** A routine shouldn't need a package manager.
- **Atomic writes.** State is written via a temp file + `os.replace`, so a
  crash mid-run can't leave corrupted JSON.
- **Bounded by construction.** Fixed-shape rows + retention + hard cap.
- **No magic.** URL and title normalization are explicit and reviewable.

## License

MIT — do whatever is useful.
