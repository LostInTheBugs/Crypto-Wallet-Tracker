# Token usage tracking — Crypto-Wallet-Tracker

LLM token usage for this project, tallied session by session.

## Cumulative tally (2026-08-02)

| Metric | Value |
|---|---|
| Dev sessions (Hermes) | 38 |
| Scripted agent sessions (API) | 12 |
| Models | deepseek-v4-flash, deepseek-v4-pro |
| Messages | 2 711 |
| API calls | 1 226 |
| Input tokens | 1 221 582 |
| Output tokens | 513 465 |
| Of which reasoning | 9 685 |
| Cache read (cache_read) | 88 492 544 |
| Cache write (cache_write) | 0 |
| **Total (input + output)** | **1 735 047** |
| Estimated cost | ≈ 3.779 USD |

> Repo created 2026-07-13; the very first sessions (Jul 13–16) are not tracked in the local DB. The tally covers the tracked dev sessions (Jul 17–19) plus later scripted sessions.

## How to re-read the counter

The Hermes session database (SQLite) holds the exact counters:

```bash
sqlite3 ~/.hermes/state.db "SELECT id, started_at, model,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  reasoning_tokens, estimated_cost_usd
  FROM sessions WHERE cwd = '/opt/hermes-work/repo'
  ORDER BY started_at;"
```

After each dev session, copy the matching row into the table above.

## Notes

- Tally taken from `~/.hermes/state.db` (table `sessions`) — these are the
  real runtime counters, not an estimate.
- « Scripted agent sessions (API) » = `api-*` sessions driven by scripts
  (audits, releases, background tasks) attached to this project.
- `reasoning_tokens` is probably included in `output_tokens`
  (to be confirmed with the provider).
- Tally generated on 2026-08-02 from the session database.
