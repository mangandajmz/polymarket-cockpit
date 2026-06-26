# Handoff

## Current Snapshot

- Date: 2026-06-26 America/Vancouver
- Branch: `main`
- Last commit reviewed: `1803b58 Record overnight bot check-in`
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
- Latest health check before poll-funnel restart reports build `1803b58`, heartbeat at
  `2026-06-26 23:26:54 UTC`, active watchlist 5, API failures 0, and no invariant issues.
- Active watchlist: GRIMDRIP, endlessFate, fishalive, frostrizz, mintblade.
- Recommendations, opportunities, positions, copied fills, and trader stats are still
  all at 0 rows.
- Live Data API probe answered the current decision funnel: all 5 active wallets fetched
  50 trade rows each, but 0 rows were fresh within the bot's 5-minute polling window;
  therefore no trades reached BUY/size/risk/recommendation filtering during the check.
- This change adds persisted `poll_funnel` telemetry so future `health_check.py` runs show
  fetched rows, fresh trades, fresh BUYs, min-whale BUYs, processed rows, and latest trade
  age per watched trader.
- Local ignored `bot_state.db` is the live state store for the overnight run.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Commit and push the poll-funnel telemetry, then restart the scheduled task so the live
   bot process writes the new `poll_funnel` state.
2. Re-run `python health_check.py`; the Poll Funnel section should show whether the bot is
   seeing fresh watched trades, fresh BUYs, and min-whale BUYs.
3. Continue the evidence window if fresh watched trades remain at 0; tune watchlist breadth
   only after the funnel shows a sustained data-volume problem.
4. Run `python opportunity_replay.py --db bot_state.db` and
   `python daily_evaluation_report.py --db bot_state.db --days 7` again after opportunities appear.
5. Decide whether to inspect dashboard views or archive the legacy repo.

## Open Questions

- If poll-funnel telemetry keeps showing 0 fresh watched trades, should Phase 1 widen watchlist breadth before loosening risk filters?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Do we archive the old `mangandajmz/polymarket-bot` repo after the overnight check passes?

## Last Verification

- `python health_check.py` passed before restart: active watchlist 5, fresh heartbeat,
  API failures 0, no invariant issues, no poll-funnel telemetry yet.
- Live Data API probe for the active watchlist showed 250 fetched trade rows, 250 trade rows,
  0 fresh rows within 5 minutes, 0 fresh BUYs, and 0 fresh BUYs above the $1,000 whale threshold.
- `python -m pytest -q` passed: 53 tests.
- Adversarial review: the absence of recommendations is currently explained by watched-trader
  inactivity/staleness, not by downstream BUY, market, price, risk, or recommendation filters.
