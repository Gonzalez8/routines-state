# Integrating with a Claude Code Routine

The scripts in `/scripts` are pure-stdlib Python 3.9+ and are meant to be
invoked from a daily routine. They read/write only files inside `/state`, so
the routine's only responsibility is to commit those files back to git.

## Minimal daily flow

```bash
# 1. Produce candidates.json via your search step (routine-specific).
#    Shape: [{"title": "...", "url": "https://...", "topic": "claude-ai"}, ...]

# 2. Filter out anything we've already sent.
python scripts/dedupe_candidates.py \
    --input candidates.json \
    --output filtered.json \
    --topic claude-ai \
    --report dedupe_report.json

# 3. Generate and send the digest from filtered.json (routine-specific).
#    Keep track of which items actually made it into the digest
#    and write them to sent_today.json (same shape as candidates.json).

# 4. Record what was sent.
python scripts/update_state.py --input sent_today.json --topic claude-ai

# 5. Keep state bounded.
python scripts/prune_state.py

# 6. Commit the updated state.
git add state/sent_news.json
git commit -m "chore(state): daily update $(date -u +%Y-%m-%d)"
git push
```

## Tips

- **Always run `prune_state.py` last.** It's idempotent and cheap, and it's the
  only guarantee that the state file doesn't grow forever.
- **Keep `candidates.json` and `filtered.json` out of git.** They are in
  `.gitignore` on purpose — they're transient working files.
- **Scope topics clearly.** `topic` is the key for the title-similarity check.
  If two routines cover unrelated topics, give them distinct slugs so their
  dedupe pools don't cross-contaminate.
- **Inspect state while iterating** with `python scripts/load_state.py` or
  `python scripts/load_state.py --ids-only`.

## Expected item shapes

`candidates.json` and `sent_today.json` both accept:

```json
[
  { "title": "Anthropic launches Claude 4.7", "url": "https://...", "topic": "claude-ai" }
]
```

Extra fields are ignored — only `title`, `url`, and `topic` are read.
