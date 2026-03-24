"""
Polymarket Copy Trading Bot — Streamlit Dashboard (Enhanced)
=============================================================
Run on VPS:
  streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

Set .env variables before running (see .env.example).
"""
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Config from .env ──────────────────────────────────────────────────────────
PASSWORD     = os.getenv("DASHBOARD_PASSWORD", "changeme")
CSV_PATH     = Path(os.getenv("CSV_PATH", "paper_trades.csv"))
LOG_PATH     = Path(os.getenv("LOG_PATH", "bot.log"))
REFRESH_MS   = int(os.getenv("REFRESH_MS", "30000"))
DAILY_BUDGET = float(os.getenv("DAILY_BUDGET", "50.0"))
BOT_MODE     = os.getenv("BOT_MODE", "PAPER")   # PAPER or LIVE

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
TRADERS   = ["majorexploiter", "beachboy4"]

CSV_FIELDS = [
    "timestamp", "trader", "market", "outcome", "whale_side",
    "whale_size_usdc", "our_size_usdc", "price", "copy_shares",
    "status", "resolved_pnl", "condition_id", "outcome_index",
]

# Market category keyword matching
CATEGORY_KEYWORDS = {
    "Sports":   [
        "nba", "nfl", "nhl", "mlb", "soccer", "football", "basketball",
        "baseball", "hockey", "tennis", "ufc", "mma", "olympics",
        "super bowl", "world cup", "champions league", "premier league",
        "la liga", "bundesliga", "serie a", "ligue 1",
    ],
    "Politics": [
        "election", "president", "congress", "senate", "biden", "trump",
        "harris", "republican", "democrat", "vote", "poll", "governor",
        "mayor", "parliament", "primary", "inauguration",
    ],
    "Crypto":   [
        "bitcoin", "btc", "eth", "ethereum", "crypto", "solana", "sol",
        "defi", "nft", "coinbase", "binance", "polygon", "matic",
        "dogecoin", "doge", "xrp", "ripple", "altcoin",
    ],
    "Finance":  [
        "fed", "interest rate", "gdp", "inflation", "sp500", "s&p",
        "dow jones", "nasdaq", "stock", "earnings", "ipo", "recession",
        "unemployment", "cpi", "fomc",
    ],
    "Tech":     [
        "apple", "google", "amazon", "meta", "microsoft", "openai",
        "ai ", "tesla", "spacex", "chatgpt", "iphone", "android",
    ],
}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polymarket Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @media (max-width: 768px) {
    .block-container { padding: 0.5rem 0.5rem 3rem !important; }
    div[data-testid="column"] { padding: 2px !important; }
  }
  .badge-paper {
    display:inline-block; background:#e65c00; color:#fff;
    padding:6px 22px; border-radius:24px; font-size:17px;
    font-weight:700; letter-spacing:1px;
  }
  .badge-live {
    display:inline-block; background:#00a651; color:#fff;
    padding:6px 22px; border-radius:24px; font-size:17px;
    font-weight:700; letter-spacing:1px;
  }
  .badge-wrap { text-align:center; margin:6px 0 18px; }
  .badge-active {
    display:inline-block; background:#00a651; color:#fff;
    padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600;
  }
  .badge-inactive {
    display:inline-block; background:#555; color:#ccc;
    padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600;
  }
  .trader-card {
    border:1px solid #333; border-radius:10px; padding:14px 16px 4px;
    margin-bottom:8px; background:rgba(255,255,255,0.03);
  }
  .activity-wrap {
    height:580px; overflow-y:auto; padding:4px;
    border:1px solid #333; border-radius:8px;
  }
  .activity-item {
    border-left:3px solid #444; padding:5px 10px; margin:3px 0;
    font-size:12px; font-family:monospace; line-height:1.4;
    word-break:break-word;
  }
  .act-trade { border-left-color:#00a651; background:rgba(0,166,81,0.07); }
  .act-skip  { border-left-color:#555; }
  .act-error { border-left-color:#dc3545; background:rgba(220,53,69,0.07); }
  .act-ts    { color:#888; font-size:11px; }
</style>
""", unsafe_allow_html=True)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=REFRESH_MS, key="autorefresh")
except ImportError:
    st.sidebar.info(
        "Tip: `pip install streamlit-autorefresh` for automatic 30-second refresh."
    )

# ── Password gate ─────────────────────────────────────────────────────────────
def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("## 🔐 Polymarket Bot Dashboard")
        with st.form("login"):
            pwd = st.text_input("Password", type="password", placeholder="Enter password")
            if st.form_submit_button("Login", use_container_width=True):
                if pwd == PASSWORD:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Incorrect password")
    return False

if not check_password():
    st.stop()

# ── Data helpers ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_trades() -> pd.DataFrame:
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=CSV_FIELDS)
    try:
        df = pd.read_csv(CSV_PATH)
    except Exception:
        return pd.DataFrame(columns=CSV_FIELDS)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for col in ["whale_size_usdc", "our_size_usdc", "resolved_pnl", "price", "copy_shares"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60)
def fetch_price(condition_id: str, outcome_index: int) -> float | None:
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"condition_ids": condition_id},
            timeout=5,
        )
        data = r.json()
        m = data[0] if isinstance(data, list) else data
        prices = m.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        return float(prices[outcome_index])
    except Exception:
        return None


LEADERBOARD_API = "https://data-api.polymarket.com/v1/leaderboard"
PUBLIC_PROFILE_API = f"{GAMMA_API}/public-profile"


@st.cache_data(ttl=300)
def fetch_trader_live_stats(username: str) -> dict:
    """
    Fetch live trader stats from public (no-auth) Polymarket endpoints.

    Flow:
      1. Hit the leaderboard for MONTH and ALL windows to get PnL, volume, rank.
      2. Use the proxyWallet from step 1 to fetch the public profile (bio, avatar).
    """
    defaults = {
        "pnl_30d":      None,
        "vol_30d":      None,
        "rank_30d":     None,
        "pnl_all":      None,
        "vol_all":      None,
        "rank_all":     None,
        "proxy_wallet": None,
        "bio":          None,
        "profile_found": False,
    }

    def _leaderboard(period: str) -> dict | None:
        try:
            r = requests.get(
                LEADERBOARD_API,
                params={"timePeriod": period, "userName": username, "limit": 1},
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                # API may return the full leaderboard; find our trader
                if isinstance(data, list):
                    for row in data:
                        if str(row.get("userName", "")).lower() == username.lower():
                            return row
                    # If filtered correctly there may only be one entry
                    if data:
                        return data[0]
        except Exception:
            pass
        return None

    month_row = _leaderboard("MONTH")
    all_row   = _leaderboard("ALL")

    if month_row is None and all_row is None:
        return defaults

    result = dict(defaults)
    result["profile_found"] = True

    if month_row:
        result["pnl_30d"]      = float(month_row.get("pnl", 0) or 0)
        result["vol_30d"]      = float(month_row.get("vol", 0) or 0)
        result["rank_30d"]     = int(month_row.get("rank", 0) or 0)
        result["proxy_wallet"] = month_row.get("proxyWallet")

    if all_row:
        result["pnl_all"]  = float(all_row.get("pnl", 0) or 0)
        result["vol_all"]  = float(all_row.get("vol", 0) or 0)
        result["rank_all"] = int(all_row.get("rank", 0) or 0)
        if not result["proxy_wallet"]:
            result["proxy_wallet"] = all_row.get("proxyWallet")

    # Fetch public profile for bio (best-effort)
    if result["proxy_wallet"]:
        try:
            rp = requests.get(
                PUBLIC_PROFILE_API,
                params={"address": result["proxy_wallet"]},
                timeout=6,
            )
            if rp.status_code == 200:
                pd_data = rp.json()
                result["bio"] = pd_data.get("bio") or pd_data.get("pseudonym")
        except Exception:
            pass

    return result


def bot_status() -> tuple[str, str]:
    if not LOG_PATH.exists():
        return "Offline", f"Log not found at {LOG_PATH}"
    mtime = LOG_PATH.stat().st_mtime
    last_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - last_dt).total_seconds()
    last_str = last_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    if age < 120:
        return "Online", last_str
    if age < 600:
        return "Idle", last_str
    return "Offline", last_str


def classify_market(name: str) -> str:
    nl = name.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in nl for kw in keywords):
            return cat
    return "Other"


def compute_streak(resolved: pd.DataFrame) -> tuple[str, int]:
    """Return (type, count) for current WIN/LOSS streak."""
    if resolved.empty:
        return "—", 0
    statuses = resolved.sort_values("timestamp")["status"].tolist()
    last = statuses[-1]
    count = 0
    for s in reversed(statuses):
        if s == last:
            count += 1
        else:
            break
    return last, count


def compute_stats(df: pd.DataFrame) -> dict:
    resolved = df[df["status"].isin(["WIN", "LOSS"])]
    wins   = int((resolved["status"] == "WIN").sum())
    losses = int((resolved["status"] == "LOSS").sum())
    total  = wins + losses
    win_rate = wins / total * 100 if total else 0.0

    today_df       = df[df["timestamp"].dt.date == date.today()] if not df.empty else df
    today_resolved = today_df[today_df["status"].isin(["WIN", "LOSS"])]

    # Best / worst trade
    best_row  = resolved.loc[resolved["resolved_pnl"].idxmax()] if not resolved.empty else None
    worst_row = resolved.loc[resolved["resolved_pnl"].idxmin()] if not resolved.empty else None

    # Streak
    streak_type, streak_count = compute_streak(resolved)

    # Max drawdown
    if not resolved.empty:
        cum_pnl     = resolved.sort_values("timestamp")["resolved_pnl"].cumsum()
        running_max = cum_pnl.cummax()
        max_drawdown = float((cum_pnl - running_max).min())
    else:
        max_drawdown = 0.0

    # Avg win / loss / risk-reward
    win_trades  = resolved[resolved["status"] == "WIN"]["resolved_pnl"]
    loss_trades = resolved[resolved["status"] == "LOSS"]["resolved_pnl"]
    avg_win  = float(win_trades.mean())  if not win_trades.empty  else 0.0
    avg_loss = float(loss_trades.mean()) if not loss_trades.empty else 0.0
    rr_ratio = abs(avg_win / avg_loss)   if avg_loss != 0         else 0.0

    return {
        "wins":          wins,
        "losses":        losses,
        "win_rate":      win_rate,
        "all_time_pnl":  float(resolved["resolved_pnl"].sum()),
        "today_pnl":     float(today_resolved["resolved_pnl"].sum()),
        "today_spent":   float(today_df["our_size_usdc"].sum()),
        "total_trades":  len(df),
        "streak_type":   streak_type,
        "streak_count":  streak_count,
        "best_trade":    best_row,
        "worst_trade":   worst_row,
        "avg_size":      float(df["our_size_usdc"].mean()) if not df.empty else 0.0,
        "max_drawdown":  max_drawdown,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "rr_ratio":      rr_ratio,
    }


def fmt_duration(td) -> str:
    s = int(td.total_seconds())
    h, m = divmod(s, 3600)
    m //= 60
    return f"{h}h {m}m" if h else f"{m}m"


def parse_log_activity(n_lines: int = 200) -> list[dict]:
    """Parse bot.log into structured activity entries (newest first)."""
    if not LOG_PATH.exists():
        return []
    try:
        text  = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[-n_lines:]
    except Exception:
        return []

    ts_pat = re.compile(r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})")
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        ts_match = ts_pat.search(line)
        ts_str   = ts_match.group(1) if ts_match else ""
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["trade placed", "bought", "copied", "position opened"]):
            kind = "trade"
        elif any(kw in line_lower for kw in ["error", "exception", "failed", "timeout", "traceback"]):
            kind = "error"
        else:
            kind = "skip"
        entries.append({"ts": ts_str, "msg": line, "kind": kind})
    return entries


# ── Load data ─────────────────────────────────────────────────────────────────
df    = load_trades()
stats = compute_stats(df) if not df.empty else {
    "wins": 0, "losses": 0, "win_rate": 0, "all_time_pnl": 0,
    "today_pnl": 0, "today_spent": 0, "total_trades": 0,
    "streak_type": "—", "streak_count": 0,
    "best_trade": None, "worst_trade": None, "avg_size": 0,
    "max_drawdown": 0, "avg_win": 0, "avg_loss": 0, "rr_ratio": 0,
}

# ── Shared runtime values ─────────────────────────────────────────────────────
now_utc  = datetime.now(timezone.utc)
week_ago = now_utc - timedelta(days=7)
day_ago  = now_utc - timedelta(days=1)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_ov, tab_pos, tab_hist, tab_traders, tab_perf, tab_markets, tab_risk, tab_logs = st.tabs([
    "📊 Overview",
    "💼 Positions",
    "📜 Trade History",
    "👥 Trader Watchlist",
    "📈 Performance",
    "🌍 Markets",
    "⚠️ Risk",
    "🔍 Bot Logs",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tab_ov:
    if BOT_MODE.upper() == "LIVE":
        st.markdown('<div class="badge-wrap"><span class="badge-live">🔴 LIVE TRADING</span></div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="badge-wrap"><span class="badge-paper">🟠 PAPER TRADING — Simulated</span></div>',
                    unsafe_allow_html=True)

    # Row 1: core PnL metrics + streak
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Today's PnL",  f"${stats['today_pnl']:+.2f}")
    c2.metric("All-Time PnL", f"${stats['all_time_pnl']:+.2f}")
    c3.metric("Total Trades", stats["total_trades"])
    streak_icon = "🔥" if stats["streak_type"] == "WIN" else ("💀" if stats["streak_type"] == "LOSS" else "—")
    streak_label = (
        f"{stats['streak_count']} {stats['streak_type']}"
        if stats["streak_count"] else "—"
    )
    c4.metric(f"{streak_icon} Streak", streak_label)

    st.divider()

    # Best / Worst trade callouts
    col_b, col_w = st.columns(2)
    with col_b:
        st.markdown("**Best Single Trade**")
        if stats["best_trade"] is not None:
            bt = stats["best_trade"]
            mkt = str(bt["market"])[:70]
            st.success(f"${bt['resolved_pnl']:+.2f} — {mkt}")
        else:
            st.info("No resolved trades yet")

    with col_w:
        st.markdown("**Worst Single Trade**")
        if stats["worst_trade"] is not None:
            wt = stats["worst_trade"]
            mkt = str(wt["market"])[:70]
            st.error(f"${wt['resolved_pnl']:+.2f} — {mkt}")
        else:
            st.info("No resolved trades yet")

    st.divider()

    # Win rate + daily budget side by side
    wr_col, budget_col = st.columns(2)
    with wr_col:
        st.subheader("Win Rate")
        sub1, sub2 = st.columns([4, 1])
        with sub1:
            st.progress(min(stats["win_rate"] / 100, 1.0))
        with sub2:
            st.write(f"**{stats['win_rate']:.1f}%**")
        st.caption(f"{stats['wins']} wins / {stats['losses']} losses")

    with budget_col:
        st.subheader("Daily Budget")
        today_spent     = stats["today_spent"] or 0.0
        today_remaining = max(0.0, DAILY_BUDGET - today_spent)
        budget_pct      = min(today_spent / DAILY_BUDGET, 1.0) if DAILY_BUDGET else 0.0
        b1, b2 = st.columns([4, 1])
        with b1:
            st.progress(budget_pct)
        with b2:
            st.write(f"Used **${today_spent:.2f}**")
        st.caption(f"${today_remaining:.2f} remaining of ${DAILY_BUDGET:.0f} daily budget")

    st.divider()

    # Extra metrics row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Trade Size",  f"${stats['avg_size']:.2f}")
    m2.metric("Max Drawdown",    f"${stats['max_drawdown']:.2f}")
    m3.metric("Risk / Reward",   f"{stats['rr_ratio']:.2f}x" if stats["rr_ratio"] else "N/A")
    m4.metric("Total Simulated PnL", f"${stats['all_time_pnl']:+.2f}")

    st.divider()

    # Bot status
    st.subheader("Bot Status")
    status_str, last_seen = bot_status()
    icon = {"Online": "🟢", "Idle": "🟡", "Offline": "🔴"}.get(status_str, "⚪")
    s_col, s_lbl = st.columns(2)
    with s_col:
        st.markdown(f"### {icon} {status_str}")
    with s_lbl:
        st.write("Last heartbeat:")
        st.code(last_seen, language=None)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ACTIVE POSITIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab_pos:
    st.subheader("Active Positions")

    if df.empty:
        st.info("No trades recorded yet.")
    else:
        pending = df[df["status"] == "PENDING"].copy()

        if pending.empty:
            st.success("No active positions — all trades resolved.")
        else:
            agg = (
                pending.groupby(
                    ["condition_id", "outcome_index", "market", "outcome"],
                    as_index=False,
                )
                .agg(
                    traders=("trader", lambda s: ", ".join(s.unique())),
                    total_cost=("our_size_usdc", "sum"),
                    total_shares=("copy_shares", "sum"),
                    avg_entry=("price", "mean"),
                    opened=("timestamp", "min"),
                )
            )
            agg["time_open"] = agg["opened"].apply(
                lambda t: fmt_duration(now_utc - t) if pd.notna(t) else "?"
            )

            fetch_prices = st.toggle("Fetch live prices (slower)", value=True, key="fetch_px")

            if fetch_prices:
                with st.spinner("Fetching current market prices…"):
                    curr_prices = [
                        fetch_price(row.condition_id, int(row.outcome_index))
                        for row in agg.itertuples()
                    ]
                agg["current_px"] = curr_prices
            else:
                agg["current_px"] = None

            agg["unreal_pnl"] = (
                agg["total_shares"]
                * agg["current_px"].fillna(agg["avg_entry"])
                - agg["total_cost"]
            )

            rows = []
            for _, r in agg.iterrows():
                pnl   = r["unreal_pnl"]
                emoji = "🟢" if pnl >= 0 else "🔴"
                curr  = f"${r['current_px']:.4f}" if pd.notna(r["current_px"]) else "N/A"
                rows.append({
                    "":           emoji,
                    "Market":     r["market"][:50],
                    "Outcome":    r["outcome"],
                    "Trader(s)":  r["traders"],
                    "Cost ($)":   f"${r['total_cost']:.2f}",
                    "Avg Entry":  f"${r['avg_entry']:.4f}",
                    "Current":    curr,
                    "Unreal PnL": f"${pnl:+.2f}",
                    "Open For":   r["time_open"],
                })

            display = pd.DataFrame(rows)

            def _color_row(row):
                try:
                    val = float(row.get("Unreal PnL", "$0").replace("$", "").replace("+", ""))
                except ValueError:
                    val = 0
                color = "rgba(0,166,81,0.15)" if val >= 0 else "rgba(220,53,69,0.15)"
                return [f"background-color:{color}"] * len(row)

            try:
                styled = display.style.apply(_color_row, axis=1)
            except AttributeError:
                styled = display
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(agg)} active position(s)")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — TRADE HISTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab_hist:
    st.subheader("Trade History")

    if df.empty:
        st.info("No trades recorded yet.")
    else:
        f1, f2 = st.columns(2)
        with f1:
            trader_filter = st.multiselect(
                "Trader", options=TRADERS, default=TRADERS, key="hist_trader"
            )
        with f2:
            status_filter = st.multiselect(
                "Outcome", options=["WIN", "LOSS", "PENDING"],
                default=["WIN", "LOSS", "PENDING"], key="hist_status"
            )

        mask     = df["trader"].isin(trader_filter) & df["status"].isin(status_filter)
        filtered = df[mask].sort_values("timestamp", ascending=False)

        st.caption(f"Showing **{len(filtered)}** of **{len(df)}** trades")

        if not filtered.empty:
            display = filtered[[
                "timestamp", "trader", "market", "outcome",
                "whale_size_usdc", "our_size_usdc", "price", "status", "resolved_pnl",
            ]].copy()
            display["timestamp"]       = display["timestamp"].dt.strftime("%m-%d %H:%M")
            display["whale_size_usdc"] = display["whale_size_usdc"].map("${:,.0f}".format)
            display["our_size_usdc"]   = display["our_size_usdc"].map("${:.2f}".format)
            display["price"]           = display["price"].map("${:.4f}".format)
            display["resolved_pnl"]    = display["resolved_pnl"].apply(
                lambda x: f"${x:+.4f}" if pd.notna(x) and x != "" else ""
            )
            display.columns = [
                "Time", "Trader", "Market", "Outcome",
                "Whale ($)", "Our ($)", "Price", "Status", "PnL ($)",
            ]

            def _color_status(row):
                s = row["Status"]
                if s == "WIN":    c = "rgba(0,166,81,0.20)"
                elif s == "LOSS": c = "rgba(220,53,69,0.20)"
                else:             c = "rgba(255,193,7,0.12)"
                return [f"background-color:{c}"] * len(row)

            try:
                styled = display.style.apply(_color_status, axis=1)
            except AttributeError:
                styled = display
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
            )

            csv_bytes = filtered.to_csv(index=False).encode()
            st.download_button(
                "⬇️ Download as CSV",
                data=csv_bytes,
                file_name=f"trades_{date.today()}.csv",
                mime="text/csv",
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — TRADER WATCHLIST
# ══════════════════════════════════════════════════════════════════════════════
with tab_traders:
    st.subheader("Trader Watchlist")

    cols = st.columns(len(TRADERS))
    for i, trader in enumerate(TRADERS):
        tdf      = df[df["trader"] == trader].copy() if not df.empty else pd.DataFrame()
        resolved = tdf[tdf["status"].isin(["WIN", "LOSS"])] if not tdf.empty else pd.DataFrame()

        wins_t   = int((resolved["status"] == "WIN").sum())  if not resolved.empty else 0
        losses_t = int((resolved["status"] == "LOSS").sum()) if not resolved.empty else 0
        total_t  = wins_t + losses_t
        wr_t     = wins_t / total_t * 100 if total_t else 0.0
        pnl_t    = float(resolved["resolved_pnl"].sum()) if not resolved.empty else 0.0
        spent_t  = float(tdf["our_size_usdc"].sum())     if not tdf.empty     else 0.0

        week_trades   = len(tdf[tdf["timestamp"] >= week_ago]) if not tdf.empty else 0
        last_trade_dt = tdf["timestamp"].max()                  if not tdf.empty else None

        if last_trade_dt is not None and pd.notna(last_trade_dt):
            is_active      = (now_utc - last_trade_dt).total_seconds() < 86400
            last_trade_str = last_trade_dt.strftime("%Y-%m-%d %H:%M UTC")
        else:
            is_active      = False
            last_trade_str = "Never"

        badge_html = (
            '<span class="badge-active">● Active</span>'
            if is_active else
            '<span class="badge-inactive">○ Inactive</span>'
        )

        # Fetch live API data
        live = fetch_trader_live_stats(trader)

        with cols[i]:
            st.markdown(
                f'<div class="trader-card"><b>👤 {trader}</b> &nbsp; {badge_html}</div>',
                unsafe_allow_html=True,
            )

            # ── Live Polymarket data (public leaderboard API) ──────────────
            if live["profile_found"]:
                if live["bio"]:
                    st.caption(f"_{live['bio']}_")

                la, lb = st.columns(2)
                pnl_30d_str  = f"${live['pnl_30d']:+,.0f}"  if live["pnl_30d"]  is not None else "N/A"
                vol_30d_str  = f"${live['vol_30d']:,.0f}"    if live["vol_30d"]  is not None else "N/A"
                rank_30d_str = f"#{live['rank_30d']}"        if live["rank_30d"] else "N/A"
                rank_all_str = f"#{live['rank_all']}"        if live["rank_all"] else "N/A"
                la.metric("30d PnL (Global)",    pnl_30d_str)
                lb.metric("30d Volume (Global)", vol_30d_str)

                lc, ld = st.columns(2)
                lc.metric("30d Rank",    rank_30d_str)
                ld.metric("All-Time Rank", rank_all_str)
            else:
                st.caption("⚠️ Not found on Polymarket leaderboard")

            # Our copy-trade stats
            m1, m2 = st.columns(2)
            m1.metric("Trades Copied",   len(tdf))
            m2.metric("This Week",       week_trades)

            m3, m4 = st.columns(2)
            m3.metric("Our Win Rate",    f"{wr_t:.1f}%")
            m4.metric("Profit for Us",   f"${pnl_t:+.2f}")

            st.caption(f"Last trade: **{last_trade_str}**")
            st.caption(f"{wins_t}W / {losses_t}L · ${spent_t:.2f} invested")

            if total_t > 0:
                st.progress(wr_t / 100)

            # Cumulative PnL mini-chart
            if not resolved.empty:
                chart = (
                    resolved.sort_values("timestamp")[["timestamp", "resolved_pnl"]]
                    .set_index("timestamp")
                    .rename(columns={"resolved_pnl": "Cumulative PnL ($)"})
                )
                chart["Cumulative PnL ($)"] = chart["Cumulative PnL ($)"].cumsum()
                st.line_chart(chart, height=150)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — PERFORMANCE CHARTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_perf:
    st.subheader("Performance Charts")

    resolved_all = df[df["status"].isin(["WIN", "LOSS"])].copy() if not df.empty else pd.DataFrame()

    if resolved_all.empty:
        st.info("No resolved trades to chart yet.")
    else:
        resolved_all = resolved_all.sort_values("timestamp")

        # 1. Cumulative PnL over time
        resolved_all["cum_pnl"] = resolved_all["resolved_pnl"].cumsum()
        fig_pnl = px.line(
            resolved_all,
            x="timestamp",
            y="cum_pnl",
            title="Cumulative PnL Over Time",
            labels={"timestamp": "Date", "cum_pnl": "Cumulative PnL ($)"},
            color_discrete_sequence=["#00a651"],
        )
        fig_pnl.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.4)
        fig_pnl.update_layout(hovermode="x unified", showlegend=False)
        st.plotly_chart(fig_pnl, use_container_width=True)

        # 2. Rolling 10-trade win rate
        resolved_all["is_win"]  = (resolved_all["status"] == "WIN").astype(int)
        resolved_all["roll_wr"] = resolved_all["is_win"].rolling(10, min_periods=1).mean() * 100
        fig_wr = px.line(
            resolved_all,
            x="timestamp",
            y="roll_wr",
            title="Win Rate Trend (Rolling 10-Trade Average)",
            labels={"timestamp": "Date", "roll_wr": "Win Rate (%)"},
            color_discrete_sequence=["#4e9af1"],
        )
        fig_wr.add_hline(y=50, line_dash="dash", line_color="yellow", opacity=0.4,
                         annotation_text="50%")
        fig_wr.update_layout(yaxis_range=[0, 100], hovermode="x unified", showlegend=False)
        st.plotly_chart(fig_wr, use_container_width=True)

        # 3. PnL contribution per trader (bar chart)
        trader_pnl = (
            resolved_all.groupby("trader")["resolved_pnl"]
            .sum()
            .reset_index()
            .rename(columns={"resolved_pnl": "Total PnL ($)"})
        )
        fig_bar = px.bar(
            trader_pnl,
            x="trader",
            y="Total PnL ($)",
            title="PnL Contribution per Trader",
            color="Total PnL ($)",
            color_continuous_scale=["#dc3545", "#333333", "#00a651"],
            text_auto=".2f",
            labels={"trader": "Trader"},
        )
        fig_bar.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_bar, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — MARKET BREAKDOWN
# ══════════════════════════════════════════════════════════════════════════════
with tab_markets:
    st.subheader("Market Breakdown")

    if df.empty:
        st.info("No trades recorded yet.")
    else:
        df_cat = df.copy()
        df_cat["category"] = df_cat["market"].apply(classify_market)

        pie_col, table_col = st.columns(2)

        with pie_col:
            cat_counts = df_cat["category"].value_counts().reset_index()
            cat_counts.columns = ["Category", "Count"]
            fig_pie = px.pie(
                cat_counts,
                names="Category",
                values="Count",
                title="Trades by Category",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig_pie, use_container_width=True)

        with table_col:
            resolved_cat = df_cat[df_cat["status"].isin(["WIN", "LOSS"])]
            if not resolved_cat.empty:
                # Win rate per category
                cat_stats_rows = []
                for cat, grp in resolved_cat.groupby("category"):
                    wins_c = int((grp["status"] == "WIN").sum())
                    tot_c  = len(grp)
                    wr_c   = wins_c / tot_c * 100 if tot_c else 0.0
                    pnl_c  = float(grp["resolved_pnl"].sum())
                    cat_stats_rows.append({
                        "Category": cat,
                        "Trades":   tot_c,
                        "Wins":     wins_c,
                        "Win Rate": f"{wr_c:.1f}%",
                        "PnL ($)":  f"${pnl_c:+.2f}",
                    })
                cat_stats_df = pd.DataFrame(cat_stats_rows)
                st.markdown("**Win Rate by Category**")
                st.dataframe(cat_stats_df, use_container_width=True, hide_index=True)

                # Best performing category by PnL
                best_cat_row = max(cat_stats_rows, key=lambda r: float(r["PnL ($)"].replace("$", "").replace("+", "")))
                st.success(
                    f"Best Category: **{best_cat_row['Category']}** "
                    f"({best_cat_row['PnL ($)']} PnL, {best_cat_row['Win Rate']} win rate)"
                )
            else:
                st.info("No resolved trades to analyze by category.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — RISK METRICS
# ══════════════════════════════════════════════════════════════════════════════
with tab_risk:
    st.subheader("Risk Metrics")

    resolved_risk = df[df["status"].isin(["WIN", "LOSS"])].copy() if not df.empty else pd.DataFrame()

    largest_loss = float(resolved_risk["resolved_pnl"].min()) if not resolved_risk.empty else 0.0
    largest_win  = float(resolved_risk["resolved_pnl"].max()) if not resolved_risk.empty else 0.0

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Max Drawdown",       f"${stats['max_drawdown']:.2f}")
    r2.metric("Risk / Reward",      f"{stats['rr_ratio']:.2f}x" if stats["rr_ratio"] else "N/A")
    r3.metric("Largest Single Loss", f"${largest_loss:.2f}")
    r4.metric("Largest Single Win",  f"${largest_win:+.2f}")

    st.divider()

    burn_col, dist_col = st.columns(2)

    with burn_col:
        st.markdown("**Daily Budget Burn Rate**")
        today_spent = stats["today_spent"] or 0.0

        if not df.empty and df["timestamp"].notna().any():
            first_trade  = df["timestamp"].min()
            days_active  = max(1, (now_utc - first_trade).days)
            avg_daily    = float(df["our_size_usdc"].sum()) / days_active
        else:
            avg_daily = 0.0

        d1, d2 = st.columns(2)
        d1.metric("Avg Daily Spend",   f"${avg_daily:.2f}")
        d2.metric("Today's Spend",     f"${today_spent:.2f}")
        remaining = max(0.0, DAILY_BUDGET - today_spent)
        budget_pct = min(today_spent / DAILY_BUDGET, 1.0) if DAILY_BUDGET else 0.0
        st.progress(budget_pct)
        st.caption(f"${remaining:.2f} remaining · ${DAILY_BUDGET:.0f} daily limit")

    with dist_col:
        st.markdown("**Trade Size Distribution**")
        if not df.empty and len(df) > 1:
            fig_hist = px.histogram(
                df,
                x="our_size_usdc",
                title="Trade Size Distribution",
                labels={"our_size_usdc": "Trade Size ($)"},
                color_discrete_sequence=["#4e9af1"],
                nbins=20,
            )
            fig_hist.update_layout(showlegend=False, margin=dict(t=30))
            st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.info("Not enough trades for distribution chart.")

    st.divider()

    st.markdown("**Average Win vs Average Loss**")
    wa, wb, wc = st.columns(3)
    wa.metric("Avg Win",          f"${stats['avg_win']:.2f}")
    wb.metric("Avg Loss",         f"${stats['avg_loss']:.2f}")
    wc.metric("Risk/Reward Ratio", f"{stats['rr_ratio']:.2f}x" if stats["rr_ratio"] else "N/A")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — BOT LOGS + ACTIVITY FEED
# ══════════════════════════════════════════════════════════════════════════════
with tab_logs:
    hdr_col, btn_col = st.columns([5, 1])
    hdr_col.subheader("Bot Logs & Activity Feed")
    with btn_col:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    log_col, feed_col = st.columns([3, 2])

    with log_col:
        st.markdown("**Raw Log (Last 50 Lines)**")
        if not LOG_PATH.exists():
            st.warning("Log file not found.")
            st.code(str(LOG_PATH.resolve()), language=None)
            st.info("Set `LOG_PATH` in `.env` to point to your bot.log file.")
        else:
            try:
                text   = LOG_PATH.read_text(encoding="utf-8", errors="replace")
                lines  = text.splitlines()
                last50 = "\n".join(lines[-50:])
                st.code(last50, language=None)
                st.caption(
                    f"Last {min(50, len(lines))} of {len(lines)} lines · "
                    f"`{LOG_PATH.resolve()}`"
                )
            except Exception as e:
                st.error(f"Error reading log file: {e}")

    with feed_col:
        st.markdown("**Live Activity Feed**")
        st.caption("🟢 Trade placed · ⬜ Poll/check · 🔴 Error")
        activity = parse_log_activity(200)
        if not activity:
            st.info("No log activity found.")
        else:
            kind_map = {"trade": "act-trade", "error": "act-error", "skip": "act-skip"}
            kind_icon = {"trade": "🟢 ", "error": "🔴 ", "skip": ""}
            feed_html = '<div class="activity-wrap">'
            for entry in activity[:100]:
                cls  = kind_map.get(entry["kind"], "act-skip")
                icon = kind_icon.get(entry["kind"], "")
                ts   = (
                    f'<span class="act-ts">{entry["ts"]}</span><br>'
                    if entry["ts"] else ""
                )
                msg  = entry["msg"].replace("<", "&lt;").replace(">", "&gt;")
                feed_html += (
                    f'<div class="activity-item {cls}">'
                    f'{ts}{icon}{msg}'
                    f"</div>"
                )
            feed_html += "</div>"
            st.markdown(feed_html, unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    f"🕐 Last loaded: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
    f"· Auto-refreshes every {REFRESH_MS // 1000}s"
)
