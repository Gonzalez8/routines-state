# Integrating with a Claude Code Routine

The scripts in `/scripts` are pure-stdlib Python 3.9+ and are meant to be
invoked from any Claude Code Routine that needs to remember whether it
has already handled something. They read/write only files inside
`/state`, so the routine's only responsibility is to commit those files
back to git.

## Pick a routine slug

Every routine that uses this store must choose a short, stable slug and
pass it via `--routine` (or include it per-item in the input JSON).

Examples: `claude-news`, `gh-monitor`, `oncall-alerts`, `blog-crawler`.

The slug becomes part of `event_id`, so treat it as a **stable namespace**
— renaming it later makes past entries look "unknown" to the dedupe
logic, which effectively resets the memory for that routine.

## Minimal daily flow

```bash
# 1. Produce candidates.json via your search/fetch/detect step.
#    Shape: [{"title": "...", "url": "...", "routine": "<slug>", "topic": "<optional>"}, ...]

# 2. Filter out anything the store has already seen.
python scripts/dedupe_candidates.py \
    --input candidates.json \
    --output filtered.json \
    --routine <slug> \
    [--topic <topic>] \
    [--report dedupe_report.json]

# 3. Act on filtered.json (send digest, open ticket, notify, ...).
#    Track what you actually handled in handled.json (same shape).

# 4. Record what was handled.
python scripts/update_state.py --input handled.json --routine <slug>

# 5. Keep state bounded.
python scripts/prune_state.py

# 6. Commit the updated state.
git add state/events.json
git commit -m "chore(state): $(date -u +%Y-%m-%d) <slug>"
git push
```

## Tips

- **Always run `prune_state.py` last.** It's idempotent and cheap, and
  it's the only guarantee that the state file doesn't grow forever.
- **Keep transient files out of git.** `candidates.json`, `filtered.json`,
  `handled.json`, etc. are already in `.gitignore` — treat them as
  ephemeral working files.
- **Use distinct routine slugs.** Never reuse a slug across unrelated
  workflows; that defeats the namespacing.
- **`topic` is optional.** Use it when you have a natural sub-grouping
  within a routine (a topic name, a repo name, a channel). Leave it
  blank otherwise.
- **Scope pruning if you want.** `prune_state.py --routine <slug>` only
  touches items for that routine. Useful when a specific routine needs a
  different retention window.
- **Inspect during development:** `python scripts/load_state.py
  --routine <slug> --ids-only`.

## Item shapes

### Candidates / handled input

Either a bare list or `{"items": [...]}`:

```json
[
  {
    "title": "Anthropic launches Claude 4.7",
    "url": "https://anthropic.com/news/claude-4-7",
    "routine": "claude-news",
    "topic": "claude-ai"
  }
]
```

`routine` can be omitted if you pass `--routine` on the CLI; it will be
used as the default. Same for `topic`. Extra fields are ignored.

### State

The canonical item shape is documented in
[memory-design.md](memory-design.md). Only eight fields are persisted —
anything else is stripped on write/prune to prevent bloat.

## Example routines

### News digest

```bash
python scripts/dedupe_candidates.py \
    --input candidates.json --output filtered.json \
    --routine claude-news --topic claude-ai

# ... send digest built from filtered.json ...

python scripts/update_state.py --input sent_today.json --routine claude-news --topic claude-ai
python scripts/prune_state.py
```

### GitHub monitor

```bash
python scripts/dedupe_candidates.py \
    --input prs.json --output new_prs.json \
    --routine gh-monitor --topic anthropics/anthropic-cookbook

# ... post notifications for new_prs.json ...

python scripts/update_state.py --input notified.json \
    --routine gh-monitor --topic anthropics/anthropic-cookbook
python scripts/prune_state.py --routine gh-monitor
```

### On-call alerts

```bash
python scripts/dedupe_candidates.py \
    --input incoming_alerts.json --output fresh_alerts.json \
    --routine oncall-alerts

# ... page on-call for fresh_alerts.json ...

python scripts/update_state.py --input paged.json --routine oncall-alerts
python scripts/prune_state.py --routine oncall-alerts
```

All three can share the same `state/events.json` safely — `event_id` is
namespaced by `routine`, so they cannot collide.
