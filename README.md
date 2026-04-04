# Polymarket Copy Trading Bot

A paper-trading (and eventually live) copy-trading bot for [Polymarket](https://polymarket.com).
It automatically discovers high-performing traders, mirrors their qualifying BUY trades at a conviction-scaled size, and logs everything to a CSV.
A Streamlit dashboard lets you monitor performance from any browser.

> **Current mode: PAPER TRADING — no real money, no wallet connected.**

---

## Overview

The bot watches the top monthly-PNL traders on Polymarket in real time. When a qualifying whale makes a large BUY, the bot copies it immediately at a fraction of the size — scaled by how big that trade is relative to the whale's recent history. All simulated trades, PnL, and market outcomes are logged to `paper_trades.csv` and displayed on a live Streamlit dashboard.

---

## How it works

### 1. Whale discovery (every 6 hours)
The `WatchlistManager` (`dynamic_watchlist.py`) fetches the top 30 traders from Polymarket's monthly PNL leaderboard, estimates each trader's win rate by sampling their last 10 positions (priced via the Gamma API), and keeps the first 5 traders that pass the 60% win-rate threshold. Qualifying addresses are cached permanently in `watchlist_cache.json` so open positions can still resolve even after a trader drops off the leaderboard.

### 2. Trade polling (every 30 seconds)
`poll_once()` fetches the last 50 trades for each watched trader. Any trade timestamped within the last 5 minutes is passed to `process_trade()`.

### 3. Trade filtering
Each candidate trade must pass every filter before being copied:

| Filter | Value | What it prevents |
|---|---|---|
| Side | BUY only | Copying exits / redemptions |
| Age | ≤ 5 minutes | Stale or replayed trades |
| Whale size | ≥ $1,000 | Low-conviction noise trades |
| Market type | No crypto keywords | Bitcoin/ETH price-direction bets |
| Trader win rate | ≥ 60% after 10 resolved trades | Copying a trader mid-slump |
| Daily loss limit | ≤ 2 losses/trader/day | Tilt-following a trader on a bad day |
| Daily cap | ≤ $60/day total | Runaway spending in a volatile session |

### 4. Conviction sizing
When a trade passes all filters, the bot calculates a conviction score:

```
conviction = whale_size_usdc / median(last_30_whale_trade_sizes)
our_bet    = min(BASE_BET × conviction, MAX_BET, daily_remaining)
```

A trade 2× the median → `$20` bet. A trade 3× the median → `$30` (capped by `MAX_BET`). This means the bot naturally bets more when a whale makes an unusually large move.

### 5. Resolution (every 60 seconds)
`resolution_loop()` checks the Gamma API for every open position. A market is considered settled when `closed == True` and the winning outcome is priced ≥ 0.99. On settlement, PnL is calculated, the position is marked WIN or LOSS, and the CSV is updated.

---

## Configuration

All values are set at the top of `paper_trading_bot.py`.

| Parameter | Default | Description |
|---|---|---|
| `MIN_WHALE_SIZE` | `1000.0` | Minimum USDC size of a whale trade to copy |
| `BASE_BET` | `10.0` | Base bet size in USD; scales with conviction |
| `MAX_BET` | `30.0` | Hard cap on a single trade in USD |
| `DAILY_LOSS_CAP` | `60.0` | Maximum net loss (gross losses − gross wins) per calendar day before new trades are blocked |
| `STARTING_BANKROLL` | `300.0` | Starting bankroll used for bankroll scaling calculations |
| `MAX_DAILY_LOSSES_PER_TRADER` | `2` | Max resolved losses from one trader per day before skipping them |
| `COPY_RATIO` | `0.10` | Legacy ratio (not used for sizing; kept for reference) |
| `WATCHLIST_TOP_N` | `5` | Number of traders to watch simultaneously |
| `WATCHLIST_MIN_WR` | `60.0` | Minimum win rate (%) to qualify for the watchlist |
| `WATCHLIST_REFRESH_H` | `6` | Hours between watchlist refreshes |
| `MIN_WIN_RATE` | `60.0` | Per-trader win rate (%) below which trades are skipped |
| `POLL_INTERVAL` | `30` | Seconds between trade polling cycles |

---

## Bankroll scaling

As the simulated bankroll grows, bet sizing should grow proportionally. The bot uses a semi-automatic scaling system — when `STARTING_BANKROLL + closed_pnl` crosses a threshold for the first time, the bot prints a prompt in the terminal and waits for confirmation before applying the new settings.

| Bankroll | BASE_BET | MAX_BET | DAILY_LOSS_CAP |
|---|---|---|---|
| $150 | $5 | $15 | $30 |
| $300 | $10 | $30 | $60 |
| $500 | $15 | $50 | $100 |
| $1,000 | $25 | $100 | $200 |
| $2,500 | $40 | $150 | $300 |

**How it works:**
- After every resolved trade, `_check_bankroll_scale()` evaluates the current bankroll.
- If a threshold is crossed for the first time this session, the bot logs `SCALE UP AVAILABLE` to `bot.log` and prints the suggested config.
- You see: `Apply new scaling? (y/n):`
- Press `y` to apply immediately (updates `BASE_BET`, `MAX_BET`, `DAILY_LOSS_CAP` in memory).
- Press `n` to decline — that threshold will not prompt again this session.
- Each threshold only prompts once per session (`bot.milestones_reached` tracks this).

> **Note:** Scaling is applied to the running process only. To make it permanent, update the values in `paper_trading_bot.py` and redeploy.

---

## Risk management

The bot has three independent capital protection mechanisms:

### 1. Minimum whale size (`MIN_WHALE_SIZE = $1,000`)
Only copy trades where the whale committed at least $1,000 USDC. This filters out casual, low-conviction trades and keeps the signal-to-noise ratio high. Raising this value makes the bot more selective; lowering it increases trade frequency but reduces average quality.

### 2. Per-trader daily loss limit (`MAX_DAILY_LOSSES_PER_TRADER = 2`)
If a specific whale has already produced 2 resolved losses for us today, all further trades from that trader are skipped until midnight UTC. This prevents the bot from following a trader through a bad day — when a whale is on a losing streak, continuing to copy them compounds losses faster than the sizing system can compensate.

### 3. Daily net loss cap (`DAILY_LOSS_CAP = $60`)
The bot tracks today's gross losses and gross wins from resolved trades. When `gross_losses − gross_wins >= DAILY_LOSS_CAP`, all new trades are blocked until midnight UTC. The key difference from a spend cap: **winning trades reduce the effective cap usage**. On a profitable day the bot can keep placing trades even if gross losses alone exceed $60, as long as wins are offsetting them. Both counters reset at midnight UTC and are restored from the CSV on restart so the cap survives bot restarts.

---

## Phase 2 auto-scaling

When `STARTING_BANKROLL + closed_pnl` exceeds `PHASE2_BANKROLL_THRESHOLD` (`$500`), the following config upgrades are suggested:

```python
WATCHLIST_TOP_N   = 10     # watch more traders
WATCHLIST_MIN_WR  = 40.0   # relax win-rate entry threshold
MIN_WHALE_SIZE    = 100.0  # copy smaller whale trades for more signals
```

Phase 1 settings are conservative for a small bankroll — fewer, higher-quality signals only. Phase 2 expands the signal universe once there is enough capital to absorb a higher variance of trade quality.

---

## Going live — checklist

Before switching from paper trading to real money:

- [ ] **Conviction median is stable**: Check that `bot.whale_sizes` has at least 30 entries (visible in bot logs). The median needs enough history to be meaningful, otherwise early trades use a single-sample median which inflates conviction scores.
- [ ] **Set `STARTING_BANKROLL`** to your actual deposit amount in USDC.
- [ ] **Verify `DAILY_LOSS_CAP`** is 15–20% of your bankroll. At $300 bankroll the default $60 cap is 20%. Adjust down if you prefer slower drawdown.
- [ ] **Run paper mode for ≥ 48 hours** and review `paper_trades.csv` — check that all whale sizes are above $1,000, conviction scores look reasonable (0.5–3.0 range is normal), and no single trader is dominating losses.
- [ ] **Audit the last 20 resolved trades** for market quality — are they politics/sports/finance or noise? Tighten `CRYPTO_KW` if unexpected market types are slipping through.
- [ ] **Connect wallet and set `LIVE_MODE = True`** on the `live` branch only. Never merge live-mode code to `main`.
- [ ] Start with `DAILY_LOSS_CAP` at 50% of the paper-mode value for the first 48 hours live.

---

## Repository layout

```
.
├── paper_trading_bot.py   # Copy-trading engine
├── dynamic_watchlist.py   # Whale discovery and watchlist management
├── backtest_configs.py    # Backtester for comparing watchlist configs
├── dashboard.py           # Streamlit monitoring dashboard
├── requirements.txt       # Python dependencies
├── deploy.sh              # VPS deploy / restart script
├── .env.example           # Environment variable template
└── .gitignore
```

> `paper_trades.csv`, `bot.log`, and `watchlist_cache.json` are **excluded from git** — they live on the VPS only.

---

## Fresh VPS deployment

Tested on Ubuntu 22.04 / 24.04 with Python 3.11+.

### 1. Provision the server

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git
```

### 2. Clone the repo

```bash
git clone https://github.com/mangandajmz/polymarket-bot.git
cd polymarket-bot
```

### 3. Install dependencies

```bash
pip3 install -r requirements.txt --no-cache-dir
```

### 4. Configure environment

```bash
cp .env.example .env
nano .env
```

Set at minimum:

```env
DASHBOARD_PASSWORD=your_strong_password_here
CSV_PATH=/home/ubuntu/polymarket-bot/paper_trades.csv
LOG_PATH=/home/ubuntu/polymarket-bot/bot.log
STATE_DB_PATH=/home/ubuntu/polymarket-bot/bot_state.db
DAILY_LOSS_CAP=60.0
STARTING_BANKROLL=300.0
BOT_MODE=PAPER
```

### 5. Create systemd service for the bot

```bash
sudo nano /etc/systemd/system/polymarket-bot.service
```

```ini
[Unit]
Description=Polymarket Copy Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket-bot
ExecStart=/usr/bin/python3 paper_trading_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/ubuntu/polymarket-bot/bot.log
StandardError=append:/home/ubuntu/polymarket-bot/bot.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-bot
sudo systemctl start polymarket-bot
sudo systemctl status polymarket-bot
```

### 6. Create systemd service for the dashboard

```bash
sudo nano /etc/systemd/system/polymarket-dash.service
```

```ini
[Unit]
Description=Polymarket Dashboard
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket-bot
ExecStart=/usr/bin/python3 -m streamlit run dashboard.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-dash
sudo systemctl start polymarket-dash
```

### 7. Open the firewall

```bash
sudo ufw allow 8501/tcp
```

Dashboard is now live at: **`http://YOUR_VPS_IP:8501`**

---

## Updating the bot

```bash
ssh ubuntu@YOUR_VPS_IP "cd /home/ubuntu/polymarket-bot && bash deploy.sh"
```

---

## Monitoring

| What | How |
|---|---|
| Dashboard | `http://YOUR_VPS_IP:8501` |
| Bot logs (live) | `sudo journalctl -u polymarket-bot -f` |
| Trade CSV | `tail -f paper_trades.csv` |
| Health summary | `python3 health_check.py` |
| Service status | `sudo systemctl status polymarket-bot polymarket-dash` |

---

## Branches

| Branch | Purpose |
|---|---|
| `main` | Stable paper trading — safe to deploy |
| `live` | Live trading — only merge here when fully tested |

PRs go to `main` first. Only promote to `live` after reviewing real-money implications.
