# Handoff

## Current Snapshot

- Date: 2026-06-24 America/Vancouver
- Branch: `main`
- Last commit reviewed: `10969a0 Document recommendation cockpit roadmap`
- Mode: local-first, paper-only recommendation cockpit
- Working tree at handoff creation: clean before this change

## Product Direction

Build the recommendation cockpit first. The project should fully explore paper
evidence until there are meaningful, measurable results. Fully automated trading
is a later addition to a proven system, not part of the current development loop.

## Hard Rules

- Every intentional project change must be reviewed and committed.
- Update this handoff before every commit.
- Stage only intentional files.
- Keep unrelated local changes out of commits.
- For code changes, run relevant tests or record why they could not be run.

## Current Operational State

- Test baseline: `python -m pytest -q` passed with 47 tests on the previous commit.
- `state/status.json` reports `RED` because no local `bot_state.db` exists yet.
- `health_check.py` currently fails for the expected missing state database.
- Next development phase starts with establishing trustworthy local runtime state.

## Next Recommended Work

1. Create local `.env` from `.env.example` if it is not already present.
2. Start the paper bot long enough to initialize or populate `bot_state.db`.
3. Run `python health_check.py` and `python property_status.py`.
4. Confirm the dashboard, health check, and status contract agree.
5. Commit the resulting reviewed development change, excluding ignored runtime data.

## Open Questions

- How long should the first paper-observation run last before we judge the runtime
  state path: smoke duration, one trading session, or a full day?
- Should the dashboard require a real local password immediately, or can initial
  development focus on bot/database health first?

## Last Verification

- Diff reviewed for `AGENTS.md`, `README.md`, `ROADMAP.md`, and `HANDOFF.md`.
- `python -m pytest -q` passed: 47 tests in 3.01s.
- Next action: initialize local runtime state and validate `bot_state.db` health.
