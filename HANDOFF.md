# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `ea33af3 Add project handoff workflow`
- Current change ready to commit: add local state initializer and commit/push discipline.
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

- Added `init_state.py` development slice to create local SQLite state without
  starting the network-dependent paper bot.
- Added tests proving initialization creates the schema/default health keys and
  preserves existing runtime values.
- `state/status.json` still reports `RED` until a real local `bot_state.db` is
  initialized in the workspace and `property_status.py` is regenerated.
- Runtime files such as `.env`, `bot_state.db`, logs, and watchlist cache remain
  ignored and should not be committed.

## Next Recommended Work

1. Run `python init_state.py` to create the ignored local `bot_state.db`.
2. Run `python health_check.py` to confirm the schema and default health surface.
3. Run `python property_status.py` to refresh `state/status.json`.
4. Configure a git remote so commits can be pushed.
5. Start a short paper-bot observation run once network/API behavior is ready to test.

## Open Questions

- How long should the first paper-observation run last before we judge the runtime
  state path: smoke duration, one trading session, or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?
- Which remote should this repository push to?

## Last Verification

- Diff reviewed for `init_state.py`, `test_init_state.py`, `README.md`, `AGENTS.md`,
  `ROADMAP.md`, and `HANDOFF.md`.
- `python -m pytest -q` passed: 49 tests in 3.53s.
- Push check: `git remote -v` returned no configured remotes, so push will be blocked
  until a remote is added.
