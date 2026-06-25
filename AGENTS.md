# Project Instructions

This repository is a local-first, paper-only Polymarket recommendation cockpit.

## Product Boundary

- Build the recommendation cockpit first.
- Explore paper evidence until there are meaningful, measurable results.
- Treat automated live trading as a later addition to a proven system.
- Do not add wallet, private-key, order-signing, or live-money execution paths
  without a separate architecture review.

## Change Discipline

Every intentional project change must be followed by review, commit, and push.

Required workflow:

1. Make the smallest coherent change.
2. Update `HANDOFF.md` with the new state, verification, and next action.
3. Review the diff adversarially.
4. Run verification appropriate to the risk.
5. Stage only intentional files.
6. Commit the reviewed change.
7. Push the commit to the configured remote.

Documentation-only changes still require a diff review. Code changes require the
relevant tests, or a clear note if tests could not be run.

Do not use `git add -A` unless the full working tree has been inspected and every
changed file is intentional.