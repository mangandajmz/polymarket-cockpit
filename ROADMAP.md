# Polymarket Cockpit Roadmap

## Current Goal

Build a recommendation cockpit first.

The near-term objective is to fully explore whether trader-following signals on
Polymarket can produce meaningful, measurable results in paper mode. The system
should help the operator understand what opportunities exist, why they are
recommended or skipped, how recommendations perform, and which strategy changes
improve the evidence.

Automation is intentionally out of scope until the recommendation system works
and has produced enough paper evidence to justify an execution layer.

## Hard Operating Rules

Every intentional project change must end with a review and a commit.

- Update HANDOFF.md with the new state, verification, and next action.
- Review the diff after each change before committing.
- Run the smallest verification that matches the risk of the change.
- Do not leave intentional work uncommitted at the end of a work session.
- Commit only reviewed, intentional files.
- Keep unrelated local changes out of the commit.

For documentation-only changes, the review may be an adversarial document review
plus a diff inspection. For code changes, the review must include the relevant
tests or a clear note explaining why tests could not be run.

## Product Direction

The cockpit should become a local analyst surface for Polymarket:

- discover and rank promising traders
- observe and persist every relevant opportunity
- explain recommendation, watch, and avoid decisions
- track open recommendations and paper positions
- resolve outcomes and compute recommendation quality
- compare heuristic rules, Bayesian trader scoring, and shadow-model signals
- make daily review and strategy tuning easy

The system should optimize for evidence quality before execution speed.

## Development Path

### 1. Establish Local Runtime State

- Create and validate a local `bot_state.db`.
- Confirm the paper bot can observe markets and persist opportunities.
- Confirm `health_check.py`, `property_status.py`, and the dashboard agree.
- Keep the system local-first and paper-only.

### 2. Trust the Recommendation Data

- Ensure every observed opportunity is recorded, including skipped trades.
- Make skip reasons and risk flags specific enough to audit later.
- Verify recommendation status, confidence, score, and suggested size are stable.
- Make unresolved opportunities and open recommendations easy to inspect.

### 3. Build the Evidence Loop

- Run daily and rolling evaluation reports.
- Replay historical opportunities before changing defaults.
- Compare current heuristic rules against Bayesian and shadow-model behavior.
- Identify which traders, categories, prices, sizes, and market types produce edge.

### 4. Improve the Cockpit

- Prioritize dashboard views that support daily operator decisions.
- Surface recommendation queue, trader quality, model disagreement, risk caps,
  unresolved markets, and recent outcomes.
- Keep the UI focused on analysis and action, not public deployment.

### 5. Prove Meaningful Results

Meaningful results should include enough paper history to answer:

- Are recommendations outperforming avoided or watched opportunities?
- Which filters add value, and which filters remove good trades?
- Which trader cohorts are reliable over time?
- Does sizing improve or damage returns?
- Are losses controlled by the current risk limits?
- Are market resolutions and PnL calculations trustworthy?

The cockpit is not considered meaningful until those answers are backed by
recorded opportunities, resolved outcomes, and repeatable reports rather than
anecdotes. Before changing default strategy behavior, the change should be
replayed or evaluated against stored data whenever possible.

## Adversarial Review Findings

This roadmap is directionally right, but the first version had important gaps:

- "Meaningful results" was not strict enough; it needed evidence gates, not vibes.
- The roadmap said what to build, but not how changes would be kept reviewable.
- Automation was correctly deferred, but the future boundary needed to preserve
  paper/live separation instead of treating execution as a toggle.
- The cockpit phase needs to protect raw opportunity capture. If skipped trades,
  failed API calls, or unresolved markets disappear from history, the dashboard
  can look persuasive while measuring the wrong thing.
- The dashboard should be treated as an operator decision surface, not a generic
  analytics page. The highest-value views are the ones that make the next action
  obvious.

## Later: Automation Boundary

Fully automated trading is a later addition to a working recommendation system.
It should not be added incrementally to the paper bot.

Before any live-money path exists, the project needs a separate architecture
review covering:

- wallet custody and private key handling
- order signing and execution safety
- dry-run/live separation
- liquidity, slippage, and order validation
- kill switches and maximum exposure controls
- audit logging
- replay and paper/live parity checks

The future automated system should reuse a proven recommendation engine, not
replace the cockpit with untested execution logic.