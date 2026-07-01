# Handoff

## Current Snapshot

- Date: 2026-06-30 America/Vancouver
- Branch: `main`
- Last commit reviewed: `2af1231 Track World Cup paper recommendations`
- Remote: `origin` -> `https://github.com/mangandajmz/polymarket-cockpit.git`
- Latest push: `main` tracks `origin/main` at `2af1231`.
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
- World Cup assistant work has started with a read-only API spike in
  `worldcup_api_spike.py`. It uses Gamma public search/markets and CLOB books,
  does not use wallet auth, does not sign anything, and does not attempt RFQ
  quote acceptance.
- Live spike on `2026 FIFA World Cup` found 5 event groups, 12 active winner
  markets, 24 CLOB token IDs, and 3 sampled books with midpoints. A
  `World Cup combo` query still found 0 combo candidates, so combo/RFQ support
  remains unproven and should not drive the next product slice yet.
- World Cup snapshots are now persisted separately from the paper bot runtime in
  ignored `worldcup_markets.db`. `worldcup_snapshot.py` stores snapshot runs,
  events, markets, tokens, and sampled CLOB books, then prints a compact local
  odds table with bid/ask/mid/spread.
- The first World Cup edge board is available. `worldcup_edge.py` reads a
  local operator CSV of `token_id,user_probability,note`, joins it to the latest
  persisted odds, and ranks `user_probability - midpoint` while supporting spread
  and minimum-edge filters. The default operator CSV `worldcup_probabilities.csv`
  is ignored so private assumptions do not get committed.
- World Cup paper recommendation tracking now saves selected edge-board rows into
  ignored `worldcup_markets.db` with thesis, status, entry midpoint, operator
  probability, edge, spread, and captured odds context. It remains paper-only and
  does not introduce wallet, signing, or live execution paths.
- World Cup recommendation resolution/evaluation is now manual and local: saved
  recommendations can be marked `WON`, `LOST`, or `VOID`, with Brier score,
  market-midpoint Brier score, and Brier edge persisted for calibration review.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed. World Cup snapshot
  runtime files `worldcup_markets.db` and `worldcup_markets.db-*` are also
  ignored.

## Next Recommended Work

1. Keep the new scheduled task running and check `python health_check.py` after
   more market activity.
2. For the World Cup assistant, add a local dashboard/report view for saved and
   resolved recommendations so edge quality can be reviewed without reading CLI
   tables.
3. Inspect the 9 fresh skipped opportunities in the dashboard or with
   `python opportunity_replay.py --db bot_state.db` to decide whether the `$1,000`
   whale threshold is too strict for the newly active watchlist.
4. Run `python daily_evaluation_report.py --db bot_state.db --days 7` after more
   opportunities resolve.
5. If fresh BUYs continue but all stay below the whale threshold, tune watchlist
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
- `python -m pytest test_worldcup_api_spike.py -q` passed: 4 tests.
- `python worldcup_api_spike.py --limit 25 --sample-price-count 3` live check
  passed: 5 event groups, 12 markets, 24 tokens, 3 sampled books.
- `python worldcup_api_spike.py --query "World Cup combo" --limit 25
  --sample-price-count 3` live check passed but found 0 combo candidates.
- `python -m py_compile worldcup_api_spike.py` passed.
- `python -m pytest -q` passed after the World Cup spike: 58 tests.
- `python -m py_compile worldcup_snapshot.py worldcup_api_spike.py` passed.
- `python -m pytest test_worldcup_snapshot.py test_worldcup_api_spike.py -q`
  passed: 7 tests.
- `python worldcup_snapshot.py --limit 25 --sample-price-count 5 --odds-limit 5`
  live check passed: saved 12 markets, 24 tokens, 5 sampled books, and printed
  an odds table.
- `python -m pytest -q` passed after the World Cup snapshot: 61 tests.
- `python -m pytest test_worldcup_edge.py -q` passed: 3 tests.
- `python -m py_compile worldcup_edge.py worldcup_snapshot.py worldcup_api_spike.py` passed.
- `python -m pytest test_worldcup_edge.py test_worldcup_snapshot.py test_worldcup_api_spike.py -q` passed: 10 tests.
- `python worldcup_edge.py --probabilities <temp-smoke-csv> --max-spread 0.05 --limit 5` passed against local `worldcup_markets.db` and printed an edge row.
- `python -m pytest -q` passed after the World Cup edge board: 64 tests.
- `python -m pytest test_worldcup_recommendations.py -q` passed: 3 tests.
- `python -m py_compile worldcup_recommendations.py worldcup_edge.py worldcup_snapshot.py worldcup_api_spike.py` passed.
- `python -m pytest test_worldcup_recommendations.py test_worldcup_edge.py test_worldcup_snapshot.py test_worldcup_api_spike.py -q` passed: 13 tests.
- `python worldcup_recommendations.py --probabilities <temp-smoke-csv> --token-id <sampled-token> --status WATCH --thesis "smoke recommendation from edge row"` passed against local ignored `worldcup_markets.db` and listed the saved paper recommendation.
- `python -m pytest -q` passed after World Cup paper recommendation tracking: 67 tests.
- `python -m pytest test_worldcup_recommendations.py -q` passed after manual resolution/evaluation: 5 tests.
- `python -m py_compile worldcup_recommendations.py worldcup_edge.py worldcup_snapshot.py worldcup_api_spike.py` passed after manual resolution/evaluation.
- `python -m pytest test_worldcup_recommendations.py test_worldcup_edge.py test_worldcup_snapshot.py test_worldcup_api_spike.py -q` passed after manual resolution/evaluation: 15 tests.
- `python worldcup_recommendations.py --probabilities <temp-smoke-csv> --token-id <sampled-token> --thesis "resolution smoke recommendation"`, `--resolve <saved-id> --result LOST`, and `--summary` passed against local ignored `worldcup_markets.db`.
- `python -m pytest -q` passed after World Cup recommendation resolution/evaluation: 69 tests.
