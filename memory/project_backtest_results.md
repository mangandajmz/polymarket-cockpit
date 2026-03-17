---
name: backtest_results_march_2026
description: Backtest results comparing 4 watchlist configs using 30-day live Polymarket data (March 2026)
type: project
---

Ran full config comparison backtest on 2026-03-17 using live Polymarket API data (30-day window, 50 traders, 1,704 Gamma API price fetches).

**Why:** To determine optimal trader selection strategy before implementing dynamic watchlist module.

**Results summary:**
| Config   | Traders | Qual.Trades | Uncapped PNL   | Bk PNL | Win%  | Consistency |
|----------|---------|-------------|----------------|--------|-------|-------------|
| Baseline | 2       | 361         | +$1,066,996    | -$35   | 77.8% | 50%         |
| Config A | 10      | 6,938       | +$1,266,690    | -$100  | 53.3% | 90%         |
| Config B | 10      | 8,759       | +$1,266,808    | -$100  | 53.6% | 90%         |
| Config C | 5       | 4,060       | +$62,576       | +$45   | 71.7% | 60%         |

**Key findings:**
- Both baseline traders (beachboy4 #2, majorexploiter #3) are confirmed top monthly earners
- majorexploiter has 100% win rate on sampled positions
- Config B wins on strategy quality (highest uncapped PNL, most trade signals, 90% consistency)
- Config C is only config with positive bankroll (+45%) on $100 — better for small capital
- Configs A/B drain $100 bankroll because whale traders deploy massive sums
- API paginates max 3500 trades (400 error at offset=3500) — high-volume traders hit this cap

**Recommended config for dynamic watchlist:**
- TOP_N=10, MIN_WIN_RATE=40%, MIN_WHALE_SIZE=$100 (Config B) for larger capital
- TOP_N=5, MIN_WIN_RATE=50%, MIN_WHALE_SIZE=$100 (Config C) for $100 bankroll

**How to apply:** When implementing dynamic watchlist, use Config B parameters as default but note that $100 bankroll will likely be exhausted quickly — recommend raising DAILY_BUDGET or BANKROLL significantly.

**Backtest script:** `backtest_configs.py` (in project root). Takes ~15-20 min to run due to Gamma API rate limits.
