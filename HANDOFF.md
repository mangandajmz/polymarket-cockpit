# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `c59945b Fix Polymarket API smoke connectivity`
- Remote: `origin` -> `https://github.com/mangandajmz/polymarket-cockpit.git`
- Latest push: `main` tracks `origin/main`.
- Mode: local-first, paper-only recommendation cockpit

## Product Direction

Build the recommendation cockpit first. The project should fully explore paper
evidence until there are meaningful, measurable results. Fully automated trading
is a later addition to a proven system, not part of the current development loop.

## Hard Rules

- Every intentional project change must be reviewed, committed, and pushed.
- Update this handoff before every commit.
- Stage only intentional files.
- Keep unrelated local changes out of commits.
- For code changes, run relevant tests or record why they could not be run.
- Push uses `git -c http.sslBackend=schannel push` on this Windows machine if
  plain `git push` hits local issuer certificate errors.

## Current Operational State

- Overnight paper-bot run is active via Windows scheduled task:
  `Polymarket Cockpit Bot Overnight`.
- Thread wake-up automation created: `polymarket-overnight-check-in` for tomorrow
  around this time.
- Latest health check after scheduled-task launch reports build `c59945b`, heartbeat
  at `2026-06-25 02:59:49 UTC`, active watchlist 5, API failures 0, and no invariant issues.
- Active watchlist: GRIMDRIP, endlessFate, fishalive, frostrizz, mintblade.
- Local ignored `bot_state.db` is the live state store for the overnight run.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Tomorrow around this time, read this handoff and run `python health_check.py`.
2. Run `python opportunity_replay.py --db bot_state.db`.
3. Run `python daily_evaluation_report.py --db bot_state.db --days 7`.
4. Run `python property_status.py`, then review/commit/push any tracked status update.
5. Decide whether to continue the evidence window, tune filters, inspect dashboard, or archive the legacy repo.

## Open Questions

- Did the overnight run capture any recommendations, skipped opportunities, or failures?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Do we archive the old `mangandajmz/polymarket-bot` repo after the overnight check passes?

## Last Verification

- Windows scheduled task `Polymarket Cockpit Bot Overnight` created and started.
- `python health_check.py` after startup passed: active watchlist 5, last poll recorded,
  API failures 0, no invariant issues.
- No code changed in this handoff update, so tests were not rerun after `c59945b`.
