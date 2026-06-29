# Polymarket Copy Trading Bot

A local-first paper-trading research cockpit for [Polymarket](https://polymarket.com).
It discovers high-performing traders, simulates copying qualifying BUY trades, records every observed opportunity to SQLite, and provides local analysis tools for improving the strategy.

> Current mode: PAPER TRADING only. No wallet, no private key, no real-money execution.

Current product goal: build a recommendation cockpit first, explore paper evidence until there are meaningful results, and treat automation as a later addition to a working system. See [ROADMAP.md](ROADMAP.md). For stop/start continuity, see [HANDOFF.md](HANDOFF.md).

---

## Local-First Posture

This project is intentionally designed to run on your own machine, not as a public VPS service.

The goals are:

- Keep strategy data, logs, trader watchlists, and simulated PnL private.
- Avoid exposing Streamlit or runtime state to the internet.
- Preserve a clean paper-trading and research loop before any live-money design exists.
- Make local operation simple enough that cloud deployment is unnecessary for the current stage.

Do not bind the dashboard to `0.0.0.0`, open firewall ports, or deploy this repo as a public web service unless a future architecture review explicitly approves that change.

---

## Holding Company Boundary

This repo is a sovereign property repo under `Claude\Projects`. It should not be imported by `holdco` and should not rely on files from the older `AI Holding Company` workspace.

The only supported holding-company interface is:

```text
state/status.json
```

Regenerate it locally with:

```powershell
python property_status.py
```

`holdco` may read that JSON contract, but it must not read Polymarket internals, runtime databases, logs, tests, or source files.

---
## What It Does

The bot watches top monthly-PNL Polymarket traders, estimates trader quality, polls their recent trades, and paper-copies qualifying BUY trades using conviction-scaled sizing. Every candidate trade is logged as an opportunity, even when skipped, so the strategy can be replayed and evaluated offline.

High-level loop:

```text
Polymarket APIs
    |
    v
dynamic_watchlist.py
    |
    v
paper_trading_bot.py
    |-- filters + sizing
    |-- simulated positions
    |-- resolution loop
    |
    v
state_store.py / bot_state.db
    |
    +--> dashboard.py
    +--> opportunity_replay.py
    +--> daily_evaluation_report.py
    +--> health_check.py
```

---

## Core Use Cases

- Run a private local paper-trading session.
- See which whale trades would be copied or skipped.
- Inspect why each opportunity was copied, skipped, veto-labeled, or resolved.
- Review open simulated positions and unrealized PnL.
- Replay historical opportunities before changing thresholds.
- Compare heuristic rules against Bayesian ranking and shadow-model scores.
- Keep all operational state on your own machine.

---

## User Stories

- As the operator, I can start the bot locally and know it is paper-only.
- As the operator, I can open a localhost dashboard without exposing it to the internet.
- As the operator, I can stop and restart the bot without losing paper-trade state.
- As the operator, I can see every decision: copied, skipped, and why.
- As the operator, I can compare current heuristic performance against shadow-model behavior.
- As the operator, I can run reports before making strategy changes.
- As the operator, I can keep strategy data, logs, and runtime state private.

---

## How It Works

### 1. Whale discovery

`WatchlistManager` in `dynamic_watchlist.py` fetches top monthly-PNL traders, estimates each trader's win rate from recent positions, and keeps the top qualifying traders. Addresses are cached locally in `watchlist_cache.json` so prior traders can still be referenced for open position resolution.

### 2. Trade polling

`poll_once()` fetches recent trades for watched traders. Recent trade events are passed to `process_trade()`.

### 3. Trade filtering

A candidate trade must pass all filters before being paper-copied:

| Filter | Default | Purpose |
|---|---:|---|
| Side | BUY only | Avoid copying exits and redemptions |
| Age | <= 5 minutes | Avoid stale/replayed trades |
| Whale size | >= $1,000 | Avoid low-conviction noise |
| Entry price | <= 0.75 | Avoid poor risk/reward near certainty |
| Market type | no crypto/spread/futures keywords | Avoid unwanted market classes |
| Trader win rate | >= 60% after 10 resolved trades | Avoid underperforming traders |
| Trader daily losses | <= 2 losses/day | Avoid following a trader during a bad day |
| Trader daily deploy | <= $60/day | Avoid over-concentration in one trader |
| Bankroll deployment | <= 60% open exposure | Avoid over-deploying simulated bankroll |

### 4. Conviction sizing

```text
conviction = whale_size_usdc / median(last_30_whale_trade_sizes)
our_bet    = min(BASE_BET * conviction * performance_multiplier, MAX_BET, bankroll * 0.035)
```

Sizing is still paper-only. It records what the bot would have copied, not an actual order.

### 5. Resolution

The resolution loop checks CLOB market state by condition ID. When a market is closed, the current token price is used to mark the simulated position as WIN or LOSS and compute PnL.

The loop also handles stale or unresolved positions with guardrails:

| Trigger | Threshold | Action |
|---|---:|---|
| Stale flag | > 30 days | Log warning |
| Zero-price | price near 0 for > 24 hours | Force-close as LOSS |
| Max age | > 72 hours | Force-close at current price |

### 6. Research layer

- `state_store.py`: SQLite persistence for fills, positions, opportunities, daily risk, trader stats, and runtime health.
- `bayesian_stats.py`: Beta-Binomial trader ranking.
- `shadow_model.py`: Online logistic scoring for candidate trades.
- `opportunity_replay.py`: Offline replay and threshold comparison.
- `daily_evaluation_report.py`: Daily and rolling evaluation snapshots.
- `dashboard.py`: Local Streamlit cockpit for monitoring and analysis.

---

## Configuration

Most strategy constants live near the top of `paper_trading_bot.py`. Environment variables in `.env` are used for local file paths and selected runtime knobs.

| Parameter | Default | Description |
|---|---:|---|
| `MIN_WHALE_SIZE` | `1000.0` | Minimum whale USDC size to copy |
| `BASE_BET` | `10.0` | Base simulated bet size |
| `MAX_BET` | `30.0` | Max simulated bet per trade |
| `DAILY_LOSS_CAP` | `60.0` | Net resolved loss cap before new trades are blocked |
| `STARTING_BANKROLL` | `300.0` | Simulated bankroll for risk calculations |
| `MAX_DAILY_LOSSES_PER_TRADER` | `2` | Per-trader daily loss limit |
| `MAX_DAILY_DEPLOY_PER_TRADER` | `60.0` | Per-trader daily deployment limit |
| `MAX_ENTRY_PRICE` | `0.75` | Skip entries above this price |
| `MAX_DEPLOY_PCT` | `0.60` | Max open deployment as fraction of bankroll |
| `HYBRID_VETO_THRESHOLD` | `0.65` | Shadow veto threshold for copied-trade labels |
| `POLL_INTERVAL` | `30` | Seconds between polling cycles |
| `WATCHLIST_RECENT_ACTIVITY_HOURS` | `24` | Maximum latest-trade age for dynamic watchlist candidates |

Note: `DAILY_LOSS_CAP` is a net resolved loss cap, not a total daily spend cap.

---

## Local Setup

Tested with Python 3.11.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` if you want custom local paths or dashboard settings.

---

## Running Locally

Start the paper bot:

```powershell
python paper_trading_bot.py
```

Install or refresh the local Windows scheduled task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_paper_bot_task.ps1
```

In a second terminal, start the local dashboard:

```powershell
python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
```

By default, the localhost dashboard opens without a password. Set
`DASHBOARD_PASSWORD` in `.env` only if you want a local login gate.

Open the dashboard at:

```text
http://127.0.0.1:8501
```

Stop either process with `Ctrl+C`.

---

## Local Monitoring

| Task | Command |
|---|---|
| Initialize state DB | `python init_state.py` |
| Health summary | `python health_check.py` |
| Replay opportunities | `python opportunity_replay.py --db bot_state.db` |
| Daily evaluation | `python daily_evaluation_report.py --db bot_state.db --days 7` |
| Force-resolve stale position | `python force_resolve.py --help` |
| Run tests | `python -m pytest -q` |

Runtime files are local-only and ignored by git:

- `bot_state.db`
- `bot_state.db-*`
- `paper_trades.csv`
- `live_trades.csv`
- `bot.log`
- `seen_hashes.json`
- `watchlist_cache.json`

---

## Repository Layout

```text
.
|-- paper_trading_bot.py       # Paper-trading engine
|-- dynamic_watchlist.py       # Trader discovery and watchlist management
|-- dashboard.py               # Local Streamlit monitoring cockpit
|-- state_store.py             # SQLite-backed state persistence
|-- api_client.py              # HTTP client with retries/backoff
|-- bayesian_stats.py          # Beta-Binomial trader ranking
|-- shadow_model.py            # Online logistic trade scorer
|-- opportunity_replay.py      # Offline replay/analysis CLI
|-- daily_evaluation_report.py # Daily and rolling evaluation reports
|-- category_utils.py          # Market category classification
|-- health_check.py            # One-shot local health summary
|-- force_resolve.py           # Manual stale-position resolution tool
|-- requirements.txt           # Python dependencies
|-- .env.example               # Local environment template
|-- .gitignore                 # Secrets/runtime state exclusions
`-- test_*.py                  # Unit and integration-style tests
```

---

## Paper-Only Boundary

This repository should stay paper-only until a separate live-trading design is reviewed.

Before any live-money work exists, require a fresh architecture review covering:

- wallet custody and key handling
- order signing and execution safety
- kill switch behavior
- dry-run/live separation
- audit logging
- maximum loss controls
- testing and replay against historical data

Do not add live trading as an incremental tweak to the paper bot.
