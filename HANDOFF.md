# Handoff

## Current Snapshot

- Date: 2026-06-25 America/Vancouver
- Branch: `main`
- Last commit reviewed: `bd8137b Record overnight bot run handoff`
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

- Overnight paper-bot run remains active via Windows scheduled task:
  `Polymarket Cockpit Bot Overnight`.
- Thread wake-up automation `polymarket-overnight-check-in` completed and was deleted
  after the check-in.
- Latest health check reports build `bd8137b`, heartbeat at
  `2026-06-25 19:46:32 UTC`, active watchlist 5, API failures 0, and no invariant issues.
- Active watchlist: GRIMDRIP, endlessFate, fishalive, frostrizz, mintblade.
- Recommendations, opportunities, positions, copied fills, and trader stats are still
  all at 0 rows after the overnight run.
- Local ignored `bot_state.db` is the live state store for the overnight run.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Continue the evidence window long enough to see whether qualified opportunities appear
   without loosening filters prematurely.
2. Add or inspect skip-reason telemetry if the bot remains healthy but still captures no
   opportunities.
3. Run `python opportunity_replay.py --db bot_state.db` and
   `python daily_evaluation_report.py --db bot_state.db --days 7` again after more live time.
4. Run `python property_status.py`, then review/commit/push any tracked status update.
5. Decide whether to tune filters, inspect dashboard views, or archive the legacy repo.

## Open Questions

- How many otherwise-interesting trades are being rejected by the current filters, and why?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Do we archive the old `mangandajmz/polymarket-bot` repo after the overnight check passes?

## Last Verification

- `python health_check.py` passed during the check-in: active watchlist 5, fresh heartbeat,
  API failures 0, no invariant issues.
- `python opportunity_replay.py --db bot_state.db` returned no opportunities.
- `python daily_evaluation_report.py --db bot_state.db --days 7` returned no opportunities.
- Scheduled task and Python process were confirmed running.
