# Polymarket Copy Trading Bot

A paper-trading (and eventually live) copy-trading bot for [Polymarket](https://polymarket.com).
It watches two high-performing traders in real time, mirrors their qualifying BUY trades at 1/10th size, and logs everything to a CSV.
A Streamlit dashboard lets you monitor performance from any browser — phone or desktop.

> **Current mode: PAPER TRADING — no real money, no wallet connected.**

---

## What it does

| Feature | Detail |
|---|---|
| **Traders copied** | `majorexploiter`, `beachboy4` |
| **Copy ratio** | 10 : 1 (whale trades $300 → we simulate $30) |
| **Daily budget** | $50 simulated USD, resets at midnight UTC |
| **Min whale size** | $30 USDC per trade |
| **Filters** | BUY-only · max 5 minutes old · no crypto markets |
| **Poll interval** | Every 30 seconds |
| **Resolution** | Checks Polymarket Gamma API every 60 s; marks WIN / LOSS |
| **Dashboard** | Streamlit on port 8501, password-protected, auto-refreshes |

---

## Repository layout

```
.
├── paper_trading_bot.py   # The bot (copy-trading engine)
├── dashboard.py           # Streamlit monitoring dashboard
├── requirements.txt       # All Python dependencies
├── deploy.sh              # VPS deploy / restart script
├── .env.example           # Environment variable template
└── .gitignore
```

> `paper_trades.csv` and `bot.log` are **excluded from git** — they live on the VPS only.

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
git clone https://github.com/YOUR_USERNAME/polymarket-bot.git
cd polymarket-bot
```

### 3. Install dependencies

```bash
pip3 install -r requirements.txt
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
DAILY_BUDGET=50.0
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

Pull and restart in one command:

```bash
bash deploy.sh
```

Or manually:

```bash
git pull origin main
sudo systemctl restart polymarket-bot polymarket-dash
```

---

## Switching from paper trading to live

> **Do this carefully. Real money is at stake.**

### Step 1 — Switch to the `live` branch

```bash
git checkout live
git pull origin live
```

### Step 2 — Connect a Polymarket wallet

Polymarket uses an embedded wallet via Magic.link. You need to:

1. Create a Polymarket account and fund it with USDC on Polygon.
2. Export your wallet's private key or use the Polymarket API key / proxy wallet.
3. Add credentials to `.env` — **never commit `.env`**.

### Step 3 — Update bot config

In `paper_trading_bot.py` (live branch), the following constants will need real values:

```python
LIVE_MODE      = True          # flip this flag
WALLET_ADDRESS = "0x..."       # your proxy wallet
PRIVATE_KEY    = os.getenv("PRIVATE_KEY")   # from .env only
DAILY_BUDGET   = 50.0          # real USD — start small
```

### Step 4 — Set BOT_MODE in .env

```env
BOT_MODE=LIVE
```

### Step 5 — Restart

```bash
bash deploy.sh
```

The dashboard mode badge will change from **PAPER** (orange) to **LIVE** (green).

> Recommended: run live with `DAILY_BUDGET=10.0` for the first week.
> Audit every trade. Only increase budget once you're confident.

---

## Monitoring

| What | How |
|---|---|
| Dashboard | `http://YOUR_VPS_IP:8501` |
| Bot logs (live) | `sudo journalctl -u polymarket-bot -f` |
| Trade CSV | `tail -f paper_trades.csv` |
| Service status | `sudo systemctl status polymarket-bot polymarket-dash` |

---

## Branches

| Branch | Purpose |
|---|---|
| `main` | Stable paper trading — safe to deploy |
| `live` | Live trading — only merge here when fully tested |

PRs go to `main` first. Only promote to `live` after reviewing real-money implications.
