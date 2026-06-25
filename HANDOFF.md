# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `9b2efd0 Record GitHub remote handoff`
- Current change ready to commit: Polymarket API smoke fix and successful paper-bot startup smoke.
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

- Local ignored `bot_state.db` exists and was created with `python init_state.py`.
- API client now ignores ambient proxy settings by default and uses `truststore`
  for secure HTTPS certificate validation on Windows.
- Paper bot smoke reached dynamic watchlist startup, selected 5 active traders,
  seeded trade history, entered monitoring, and wrote heartbeat/health.
- `python health_check.py` reports active count 5, API failures 0, and no invariant issues.
- `state/status.json` remains `GREEN` with `state_db_present: 1`, 0 open
  recommendations, and 0 open paper positions.
- Runtime files such as `.env`, `bot_state.db`, `paper_trades.csv`, logs, and
  watchlist cache remain ignored and should not be committed.

## Next Recommended Work

1. Decide whether to run a longer paper-observation window: one session or full day.
2. Inspect the dashboard against the initialized database and active watchlist.
3. Re-run `python health_check.py` after the longer observation window.
4. Review whether stale local proxy variables should be documented for future setup.
5. Archive the legacy `mangandajmz/polymarket-bot` repo after confirming the new repo is complete.

## Open Questions

- How long should the first real evidence window run: one session or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Do we archive the old `mangandajmz/polymarket-bot` repo now or after one successful
  longer paper-observation session in the new repo?

## Last Verification

- Diagnostic direct API request showed Polymarket leaderboard/trades endpoints return 200
  when stale proxy inheritance is bypassed and TLS is valid.
- `python -m pip install truststore` installed `truststore-0.10.4`.
- `JsonApiClient` live smoke returned a Polymarket leaderboard list securely.
- `python paper_trading_bot.py` smoke passed watchlist startup and entered monitoring.
- `python health_check.py` passed after smoke: active watchlist 5, last poll recorded,
  API failures 0, no invariant issues.
- `python property_status.py` refreshed `state/status.json`.
- `python -m pytest -q` passed: 51 tests in 3.16s.
