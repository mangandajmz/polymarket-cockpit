# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `8f40493 Add local state initializer`
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

- `init_state.py` is committed. It creates local SQLite state without starting
  the network-dependent paper bot.
- `test_init_state.py` is committed. It verifies schema/default health creation
  and preservation of existing runtime values.
- `state/status.json` still reports `RED` until a real local `bot_state.db` is
  initialized in the workspace and `property_status.py` is regenerated.
- Runtime files such as `.env`, `bot_state.db`, logs, and watchlist cache remain
  ignored and should not be committed.

## Next Recommended Work

1. Configure a git remote so commits can be pushed.
2. Run `python init_state.py` to create the ignored local `bot_state.db`.
3. Run `python health_check.py` to confirm the schema and default health surface.
4. Run `python property_status.py` to refresh `state/status.json`.
5. Start a short paper-bot observation run once network/API behavior is ready to test.

## Open Questions

- Which remote should this repository push to?
- How long should the first paper-observation run last before we judge the runtime
  state path: smoke duration, one trading session, or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?

## Last Verification

- `python -m pytest -q` passed before `8f40493`: 49 tests in 3.53s.
- `git push` after `8f40493` failed: no configured push destination.
- This handoff-only follow-up needs diff review; tests are not rerun because no
  code changed after the passing suite.
