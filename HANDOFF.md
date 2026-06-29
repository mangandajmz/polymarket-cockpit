# Handoff

## Current Snapshot

- Date: 2026-06-28 America/Vancouver
- Branch: `main`
- Last commit reviewed: `1bdec5e Add poll funnel telemetry`
- Remote: `origin` -> `https://github.com/mangandajmz/polymarket-cockpit.git`
- Latest push before this change: `main` tracks `origin/main`.
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
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Commit and push this active-watchlist/scheduler fix.
2. Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_paper_bot_task.ps1`
   to install/start the durable local scheduled task.
3. Re-run `python health_check.py` after one or two poll cycles; the Poll Funnel
   section should show fresh telemetry on the committed build.
4. If recommendations remain at 0 after fresh watched trades appear, inspect the
   downstream BUY/size/price/category/risk filters using `opportunity_replay.py`.
5. After at least several opportunities are logged, run
   `python daily_evaluation_report.py --db bot_state.db --days 7`.

## Open Questions

- Is 24 hours the right activity gate, or should Phase 1 bias harder toward very
  recent traders, e.g. 6-12 hours?
- Should watchlist ranking evolve from filter-by-recency to a weighted PNL / WR /
  freshness score after initial evidence arrives?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?

## Last Verification

- `python -m pytest test_watchlist_hardening.py -q` passed: 3 tests.
- `python -m py_compile dynamic_watchlist.py paper_trading_bot.py` passed.
- PowerShell parser check passed for `scripts/run_paper_bot.ps1` and
  `scripts/install_paper_bot_task.ps1`.
- `python -m pytest -q` passed: 54 tests. Scheduled-task installation is pending until after commit/push.
