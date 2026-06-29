# Handoff

## Current Snapshot

- Date: 2026-06-28 America/Vancouver
- Branch: `main`
- Last commit reviewed: `858f042 Record active Polymarket bot restart`
- Remote: `origin` -> `https://github.com/mangandajmz/polymarket-cockpit.git`
- Latest push: `main` tracks `origin/main` at `858f042`.
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

- Incident root cause: the existing Windows task `Polymarket Cockpit Bot Overnight`
  is a one-time task (`Start Date: 2026-06-24`, `Next Run Time: N/A`) and ended
  with `0x8007042B` / "The process terminated unexpectedly". It was not a durable
  supervisor for the paper bot.
- A second root cause was stale watchlist selection: the active watchlist was
  dominated by high monthly-PNL wallets whose latest trades were days old, while
  lower-ranked leaderboard wallets had recent BUY activity.
- This change adds a recent-activity gate to dynamic watchlist refreshes. By
  default, candidates whose latest trade is older than 24 hours are skipped before
  win-rate estimation. The gate can be tuned with `WATCHLIST_RECENT_ACTIVITY_HOURS`.
- Watchlist leaderboard headroom is now at least 60 candidates so inactive top-PNL
  wallets do not crowd out active paper-evidence candidates.
- This change adds `scripts/run_paper_bot.ps1`, which runs `paper_trading_bot.py`
  from the repo root and captures stdout/stderr into ignored `logs/paper_bot_task.log`.
- This change adds `scripts/install_paper_bot_task.ps1`, which disables the old
  one-shot overnight task, registers `Polymarket Cockpit Bot` as an at-logon task,
  enables restart-on-failure, removes the execution time limit, and starts it.
- The durable Windows task `Polymarket Cockpit Bot` is installed and running.
  `schtasks` reports status `Running`, no execution time limit, and the action
  points at `scripts/run_paper_bot.ps1`.
- Live health after restart reports build `c373632`, heartbeat at `2026-06-29
  04:30:04 UTC`, API failures 0, and no invariant issues.
- Active watchlist after the recency gate: 1two1two, swisstony, BreakTheBank,
  0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465, vanjie.
- The first live poll on the fixed build fetched 250 trade rows, saw 10 fresh BUY
  rows, processed 10 rows, and recorded 9 opportunities/recommendations.
- Current paper evidence: 9 recommendations total, 8 open WATCH recommendations,
  1 AVOID recommendation, 0 open paper positions, 0 copied fills. The new rows
  were skipped because the observed BreakTheBank trades were below the `$1,000`
  whale-size threshold.
- Dashboard operator flow is being simplified: blank/default `DASHBOARD_PASSWORD`
  now means passwordless localhost access. Setting a real `DASHBOARD_PASSWORD`
  still enables the login gate.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Keep the new scheduled task running and check `python health_check.py` after
   more market activity.
2. Inspect the 9 fresh skipped opportunities in the dashboard or with
   `python opportunity_replay.py --db bot_state.db` to decide whether the `$1,000`
   whale threshold is too strict for the newly active watchlist.
3. Run `python daily_evaluation_report.py --db bot_state.db --days 7` after more
   opportunities resolve.
4. If fresh BUYs continue but all stay below the whale threshold, tune watchlist
   quality/size thresholds before loosening copy/risk rules.

## Open Questions

- Is 24 hours the right activity gate, or should Phase 1 bias harder toward very
  recent traders, e.g. 6-12 hours?
- Should watchlist ranking evolve from filter-by-recency to a weighted PNL / WR /
  freshness score after initial evidence arrives?
- If the dashboard is ever bound beyond `127.0.0.1`, restore a real password gate
  before exposing it.

## Last Verification

- `python -m pytest test_watchlist_hardening.py -q` passed: 3 tests.
- `python -m py_compile dynamic_watchlist.py paper_trading_bot.py` passed.
- PowerShell parser check passed for `scripts/run_paper_bot.ps1` and
  `scripts/install_paper_bot_task.ps1`.
- `python -m pytest -q` passed: 54 tests.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_paper_bot_task.ps1`
  installed and started `Polymarket Cockpit Bot`.
- `python health_check.py` passed after restart: build `c373632`, fresh heartbeat,
  active watchlist 5, poll-funnel telemetry present, 10 fresh BUY rows processed,
  API failures 0, no invariant issues.
- `python property_status.py` regenerated `state/status.json`: 9 recommendations
  total, 8 open recommendations, 0 open paper positions.
- `python -m py_compile dashboard.py` passed after the passwordless dashboard change.
- `python -m pytest -q` passed after the passwordless dashboard change: 54 tests.
