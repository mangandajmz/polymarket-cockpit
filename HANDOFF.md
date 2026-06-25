# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `a822148 Update handoff after initializer commit`
- Current change ready to commit: initialized local paper state and refreshed status contract.
- Latest push attempt: blocked because no git remote is configured.
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
- Push is currently blocked until a git remote is configured; `git remote -v` is empty.

## Current Operational State

- Local ignored `bot_state.db` exists and was created with `python init_state.py`.
- `python health_check.py` reads the database successfully.
- Health status is `Initialized; paper bot has not started polling yet.`
- `state/status.json` is refreshed to `GREEN` with `state_db_present: 1`, 0 open
  recommendations, and 0 open paper positions.
- Runtime files such as `.env`, `bot_state.db`, logs, and watchlist cache remain
  ignored and should not be committed.

## Next Recommended Work

1. Configure a git remote so commits can be pushed.
2. Start a short paper-bot observation run once network/API behavior is ready to test.
3. Re-run `python health_check.py` after the bot has polled at least once.
4. Inspect the dashboard against the initialized database.
5. Decide the first evidence-gathering window: smoke duration, one session, or full day.

## Open Questions

- Which remote should this repository push to?
- How long should the first paper-observation run last before we judge the runtime
  state path: smoke duration, one trading session, or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?

## Last Verification

- `python init_state.py` created ignored local `bot_state.db`.
- `python health_check.py` passed and reported 0 open positions, 0 closed positions,
  no trader stats, and no invariant issues.
- `python property_status.py` refreshed `state/status.json` to `GREEN`.
- `python -m pytest -q` passed: 49 tests in 2.90s.
- Push remains blocked until a git remote is configured.
