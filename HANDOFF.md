# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `b4124d7 Initialize local paper state`
- Remote: `origin` -> `https://github.com/mangandajmz/polymarket-cockpit.git`
- Latest push: `main` pushed and set to track `origin/main`.
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
- `python health_check.py` reads the database successfully.
- Health status is `Initialized; paper bot has not started polling yet.`
- `state/status.json` is refreshed to `GREEN` with `state_db_present: 1`, 0 open
  recommendations, and 0 open paper positions.
- Runtime files such as `.env`, `bot_state.db`, logs, and watchlist cache remain
  ignored and should not be committed.

## Next Recommended Work

1. Start a short paper-bot observation run once network/API behavior is ready to test.
2. Re-run `python health_check.py` after the bot has polled at least once.
3. Inspect the dashboard against the initialized database.
4. Decide the first evidence-gathering window: smoke duration, one session, or full day.
5. Archive the legacy `mangandajmz/polymarket-bot` repo after confirming the new repo is complete.

## Open Questions

- How long should the first paper-observation run last before we judge the runtime
  state path: smoke duration, one trading session, or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Do we archive the old `mangandajmz/polymarket-bot` repo now or after one successful
  paper-observation session in the new repo?

## Last Verification

- `python init_state.py` created ignored local `bot_state.db`.
- `python health_check.py` passed and reported 0 open positions, 0 closed positions,
  no trader stats, and no invariant issues.
- `python property_status.py` refreshed `state/status.json` to `GREEN`.
- `python -m pytest -q` passed before `b4124d7`: 49 tests in 2.90s.
- `git -c http.sslBackend=schannel push -u origin main` succeeded.
