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
import sqlite3
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from api_client import JsonApiClient
from dotenv import load_dotenv

load_dotenv()

# ── Config from .env ──────────────────────────────────────────────────────────
PASSWORD     = os.getenv("DASHBOARD_PASSWORD", "changeme")
# Default paths are absolute relative to this file so the dashboard works
# regardless of which directory Streamlit is launched from.
_HERE        = Path(__file__).parent
CSV_PATH     = Path(os.getenv("CSV_PATH", str(_HERE / "paper_trades.csv")))
LOG_PATH     = Path(os.getenv("LOG_PATH", str(_HERE / "bot.log")))
STATE_DB_PATH = Path(os.getenv("STATE_DB_PATH", str(_HERE / "bot_state.db")))
REFRESH_MS   = int(os.getenv("REFRESH_MS", "30000"))
# Prefer DAILY_LOSS_CAP (new name); fall back to DAILY_CAP / DAILY_BUDGET for
# VPS .env files that haven't been updated yet.
DAILY_LOSS_CAP = float(
    os.getenv("DAILY_LOSS_CAP") or os.getenv("DAILY_CAP") or os.getenv("DAILY_BUDGET") or "60.0"
)
STARTING_BANKROLL = float(os.getenv("STARTING_BANKROLL", "300.0"))
BOT_MODE          = os.getenv("BOT_MODE", "PAPER")   # PAPER or LIVE
MIN_WIN_RATE      = 60.0  # % threshold — must match paper_trading_bot.py

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
TRADERS   = ["majorexploiter", "beachboy4"]
POSITION_KEYS = ["trader", "condition_id", "outcome_index"]
HTTP = JsonApiClient(default_timeout=8.0, default_retries=3, backoff_base=1.0, jitter_max=0.2)


def get_build_version() -> str:
    env_version = os.getenv("APP_BUILD_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_HERE,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"

CSV_FIELDS = [
    "timestamp", "trader", "market", "outcome", "whale_side",
    "whale_size_usdc", "our_size_usdc", "price", "copy_shares",
    "conviction", "status", "resolved_pnl", "condition_id", "outcome_index",
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
            if st.form_submit_button("Login", width="stretch"):
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
    if STATE_DB_PATH.exists():
        try:
            with sqlite3.connect(STATE_DB_PATH) as conn:
                df = pd.read_sql_query(
                    """
                    SELECT
                        timestamp_utc AS timestamp,
                        trader,
                        market,
                        outcome,
                        whale_side,
                        whale_size_usdc,
                        our_size_usdc,
                        price,
                        copy_shares,
                        conviction,
                        status,
                        resolved_pnl,
                        condition_id,
                        outcome_index,
                        event_id,
                        position_id
                    FROM copied_fills
                    ORDER BY timestamp_utc
                    """,
                    conn,
                )
        except Exception as exc:
            st.error(f"Could not read state database: {exc}")
            return pd.DataFrame(columns=CSV_FIELDS)
    elif CSV_PATH.exists():
        try:
            df = pd.read_csv(CSV_PATH)
        except Exception as exc:
            st.error(f"Could not read CSV trade log: {exc}")
            return pd.DataFrame(columns=CSV_FIELDS)
    else:
        return pd.DataFrame(columns=CSV_FIELDS)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for col in ["whale_size_usdc", "our_size_usdc", "resolved_pnl", "price", "copy_shares"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=30)
def load_open_positions() -> pd.DataFrame:
    if not STATE_DB_PATH.exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(STATE_DB_PATH) as conn:
            df = pd.read_sql_query(
                """
                SELECT
                    position_id,
                    trader,
                    condition_id,
                    outcome_index,
                    market,
                    outcome,
                    opened_at_utc,
                    updated_at_utc,
                    status,
                    total_cost,
                    total_shares,
                    last_price,
                    pnl,
                    close_reason
                FROM positions
                WHERE status = 'OPEN'
                ORDER BY opened_at_utc
                """,
                conn,
            )
    except Exception as exc:
        st.error(f"Could not read open positions from state database: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    df["opened_at_utc"] = pd.to_datetime(df["opened_at_utc"], utc=True, errors="coerce")
    df["updated_at_utc"] = pd.to_datetime(df["updated_at_utc"], utc=True, errors="coerce")
    return df


@st.cache_data(ttl=15)
def load_runtime_kv() -> dict:
    if not STATE_DB_PATH.exists():
        return {}
    try:
        with sqlite3.connect(STATE_DB_PATH) as conn:
            rows = conn.execute("SELECT key, value FROM kv_state").fetchall()
    except Exception:
        return {}
    out = {}
    for key, value in rows:
        try:
            out[key] = json.loads(value)
        except Exception:
            out[key] = value
    return out


@st.cache_data(ttl=60)
def fetch_price(condition_id: str, outcome_index: int) -> float | None:
    try:
        data = HTTP.get_json(f"{CLOB_API}/markets/{condition_id}", timeout=5, retries=2)
        if not isinstance(data, dict):
            return None
        tokens = data.get("tokens") or []
        if outcome_index >= len(tokens):
            return None
        return float(tokens[outcome_index].get("price", 0))
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
            data = HTTP.get_json(
                LEADERBOARD_API,
                params={"timePeriod": period, "userName": username, "limit": 1},
                timeout=8,
                retries=3,
            )
            # API may return the full leaderboard; find our trader
            if isinstance(data, list):
                for row in data:
                    if str(row.get("userName", "")).lower() == username.lower():
                        return row
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
            pd_data = HTTP.get_json(
                PUBLIC_PROFILE_API,
                params={"address": result["proxy_wallet"]},
                timeout=6,
                retries=2,
            )
            if isinstance(pd_data, dict):
                result["bio"] = pd_data.get("bio") or pd_data.get("pseudonym")
        except Exception:
            pass

    return result


def bot_status() -> tuple[str, str]:
    if STATE_DB_PATH.exists():
        try:
            with sqlite3.connect(STATE_DB_PATH) as conn:
                row = conn.execute(
                    "SELECT value FROM kv_state WHERE key = 'health'"
                ).fetchone()
            if row and row[0]:
                health = json.loads(row[0])
                last_str = health.get("last_heartbeat_utc", "unknown")
                last_dt = pd.to_datetime(last_str, utc=True, errors="coerce")
                if pd.notna(last_dt):
                    age = (datetime.now(timezone.utc) - last_dt.to_pydatetime()).total_seconds()
                    if age < 120:
                        return "Online", last_str
                    if age < 600:
                        return "Idle", last_str
                    return "Offline", last_str
        except Exception:
            pass
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


def positions_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per resolved position, deduped by trader + market outcome.

    The bot stamps the total position PnL identically on every CSV row that
    belongs to the same (condition_id, outcome_index) position.  Summing or
    counting raw rows inflates all PnL totals and win/loss counts by the number
    of rows per position.  Use this helper wherever wins, losses, or
    resolved_pnl are aggregated — never operate on the raw resolved rows directly.
    """
    resolved = df[df["status"].isin(["WIN", "LOSS"])]
    if resolved.empty:
        return resolved
    return (
        resolved
        .sort_values("timestamp")
        .groupby(POSITION_KEYS, as_index=False)
        .first()
    )


def compute_stats(df: pd.DataFrame) -> dict:
    pos = positions_df(df)          # one row per resolved position
    # Win/loss count: deduplicate by trader + market outcome.
    # A position is WIN if ANY row for it has status=WIN; LOSS otherwise.
    # This guards against .first() picking a stale row when statuses differ.
    closed_rows = df[df["status"].isin(["WIN", "LOSS"])]
    if not closed_rows.empty:
        pos_status = (
            closed_rows.groupby(POSITION_KEYS)["status"]
            .apply(lambda x: "WIN" if "WIN" in x.values else "LOSS")
        )
        wins   = int((pos_status == "WIN").sum())
        losses = int((pos_status == "LOSS").sum())
    else:
        wins, losses = 0, 0
    total    = wins + losses
    win_rate = wins / total * 100 if total else 0.0

    today_df  = df[df["timestamp"].dt.date == datetime.now(timezone.utc).date()] if not df.empty else df
    today_pos = positions_df(today_df)

    # Best / worst trade — use actual proportional profit formula for best trade
    # to avoid stale whale-payout values in resolved_pnl.
    if not pos.empty:
        wins_df = df[df["status"] == "WIN"].copy()
        wins_df["actual_profit"] = wins_df["our_size_usdc"] * (1.0 / wins_df["price"] - 1.0)
        best_trade_pnl = float(
            wins_df.groupby(POSITION_KEYS)["actual_profit"].sum().max()
        ) if not wins_df.empty else 0.0
        best_row  = pos.loc[pos["resolved_pnl"].idxmax()]
        worst_row = pos.loc[pos["resolved_pnl"].idxmin()]
    else:
        best_trade_pnl = 0.0
        best_row  = None
        worst_row = None

    # Streak
    streak_type, streak_count = compute_streak(pos)

    # Max drawdown
    if not pos.empty:
        cum_pnl     = pos.sort_values("timestamp")["resolved_pnl"].cumsum()
        running_max = cum_pnl.cummax()
        max_drawdown = float((cum_pnl - running_max).min())
    else:
        max_drawdown = 0.0

    # Avg win / loss / risk-reward
    win_trades  = pos[pos["status"] == "WIN"]["resolved_pnl"]
    loss_trades = pos[pos["status"] == "LOSS"]["resolved_pnl"]
    avg_win  = float(win_trades.mean())  if not win_trades.empty  else 0.0
    avg_loss = float(loss_trades.mean()) if not loss_trades.empty else 0.0
    rr_ratio = abs(avg_win / avg_loss)   if avg_loss != 0         else 0.0

    today_wins_pos   = today_pos[today_pos["status"] == "WIN"]
    today_losses_pos = today_pos[today_pos["status"] == "LOSS"]

    return {
        "wins":              wins,
        "losses":            losses,
        "win_rate":          win_rate,
        "all_time_pnl":      float(pos["resolved_pnl"].sum()),
        "today_pnl":         float(today_pos["resolved_pnl"].sum()),
        "today_spent":       float(today_df["our_size_usdc"].sum()),
        "today_gross_wins":  float(today_wins_pos["resolved_pnl"].sum()),
        "today_gross_losses": float(today_losses_pos["resolved_pnl"].abs().sum()),
        "total_trades":      total,
        "streak_type":       streak_type,
        "streak_count":      streak_count,
        "best_trade":        best_row,
        "best_trade_pnl":    best_trade_pnl,
        "worst_trade":       worst_row,
        "avg_size":          float(closed_rows["our_size_usdc"].mean()) if not closed_rows.empty else 0.0,
        "max_drawdown":      max_drawdown,
        "avg_win":           avg_win,
        "avg_loss":          avg_loss,
        "rr_ratio":          rr_ratio,
    }


def load_watchlist_cache() -> tuple[dict, dict, str]:
    """Read watchlist_cache.json.

    Returns (active_traders, inactive_traders, last_refresh) where each traders
    dict is {name: address}.  Handles both v1 (flat string) and v2 (dict with
    'address'/'active' keys) formats.  Duplicate addresses are deduplicated:
    the active entry wins; if both have the same status, the first-seen wins.
    """
    cache_path = _HERE / "watchlist_cache.json"
    if not cache_path.exists():
        return {}, {}, "never"
    try:
        raw          = json.loads(cache_path.read_text(encoding="utf-8"))
        last_refresh = raw.get("_last_successful_refresh", "never")
        active   = {}
        inactive = {}
        seen_addrs: dict = {}   # normalised addr -> name already stored

        for name, val in raw.items():
            if name.startswith("_"):
                continue
            if isinstance(val, dict):
                addr      = val.get("address", "")
                is_active = bool(val.get("active", False))
            elif isinstance(val, str):
                addr      = val
                is_active = False   # old format: inactive until next bot refresh
            else:
                continue
            if not addr:
                continue

            addr_key = addr.lower()
            if addr_key in seen_addrs:
                # Duplicate address — active entry beats inactive, else first-seen wins
                existing_name = seen_addrs[addr_key]
                if is_active and existing_name in inactive:
                    del inactive[existing_name]
                    seen_addrs[addr_key] = name
                    active[name] = addr
                # else: existing entry is equal or better — skip this one
                continue

            seen_addrs[addr_key] = name
            if is_active:
                active[name] = addr
            else:
                inactive[name] = addr

        return active, inactive, last_refresh
    except Exception:
        return {}, {}, "never"


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
    "today_pnl": 0, "today_spent": 0,
    "today_gross_wins": 0, "today_gross_losses": 0,
    "total_trades": 0,
    "streak_type": "—", "streak_count": 0,
    "best_trade": None, "best_trade_pnl": 0, "worst_trade": None, "avg_size": 0,
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Today's PnL",  f"${stats['today_pnl']:+.2f}")
    c2.metric("All-Time PnL", f"${stats['all_time_pnl']:+.2f}")
    current_bankroll = STARTING_BANKROLL + stats["all_time_pnl"]
    c3.metric("Bankroll", f"${current_bankroll:,.2f}", delta=f"${stats['all_time_pnl']:+.2f}")
    c4.metric("Total Trades", stats["total_trades"])
    streak_icon = "🔥" if stats["streak_type"] == "WIN" else ("💀" if stats["streak_type"] == "LOSS" else "—")
    streak_label = (
        f"{stats['streak_count']} {stats['streak_type']}"
        if stats["streak_count"] else "—"
    )
    c5.metric(f"{streak_icon} Streak", streak_label)

    st.divider()

    # Best / Worst trade callouts
    col_b, col_w = st.columns(2)
    with col_b:
        st.markdown("**Best Single Trade**")
        if stats["best_trade"] is not None:
            bt = stats["best_trade"]
            mkt = str(bt["market"])[:70]
            st.success(f"${stats['best_trade_pnl']:+.2f} — {mkt}")
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
        st.subheader("Daily Risk")
        gross_wins   = stats["today_gross_wins"]  or 0.0
        gross_losses = stats["today_gross_losses"] or 0.0
        net_loss     = gross_losses - gross_wins
        loss_pct     = min(net_loss / DAILY_LOSS_CAP, 1.0) if DAILY_LOSS_CAP else 0.0
        b1, b2 = st.columns([4, 1])
        with b1:
            st.progress(max(loss_pct, 0.0))
        with b2:
            st.write(f"Net **${net_loss:+.2f}**")
        st.caption(
            f"${gross_losses:.2f} losses − ${gross_wins:.2f} wins = "
            f"${net_loss:.2f} net  (cap ${DAILY_LOSS_CAP:.0f})"
        )

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
    runtime_kv = load_runtime_kv()
    icon = {"Online": "🟢", "Idle": "🟡", "Offline": "🔴"}.get(status_str, "⚪")
    s_col, s_lbl = st.columns(2)
    with s_col:
        st.markdown(f"### {icon} {status_str}")
    with s_lbl:
        st.write("Last heartbeat:")
        st.code(last_seen, language=None)
    live_build = runtime_kv.get("health", {}).get("build_version") if isinstance(runtime_kv.get("health"), dict) else None
    st.caption(f"Dashboard build: {get_build_version()} | Bot build: {live_build or 'unknown'}")
    watchlist_health = runtime_kv.get("watchlist_health", {})
    if watchlist_health:
        st.caption(
            f"Watchlist: {watchlist_health.get('active_count', 0)} active"
            f" | last refresh {watchlist_health.get('last_successful_refresh', 'unknown')}"
        )
        if watchlist_health.get("last_error"):
            st.warning(f"Watchlist refresh warning: {watchlist_health['last_error']}")
    invariant_issues = runtime_kv.get("invariant_issues", []) or []
    if invariant_issues:
        st.error("Invariant warnings detected in bot state.")
        for issue in invariant_issues[:3]:
            st.code(issue, language=None)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ACTIVE POSITIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab_pos:
    st.subheader("Active Positions")

    if df.empty:
        st.info("No trades recorded yet.")
    else:
        open_positions = load_open_positions()
        if not open_positions.empty:
            open_positions["time_open"] = open_positions["opened_at_utc"].apply(
                lambda t: fmt_duration(now_utc - t) if pd.notna(t) else "?"
            )
            rows = []
            for _, r in open_positions.iterrows():
                pnl = float(r["pnl"] or 0.0)
                last_px = r["last_price"]
                near_zero = pd.notna(last_px) and float(last_px) < 0.05
                emoji = "⚠️" if near_zero else ("🟢" if pnl >= 0 else "🔴")
                curr = f"${float(last_px):.4f}" if pd.notna(last_px) else "N/A"
                pnl_str = f"${pnl:+.2f} ⚠️ Near Zero" if near_zero else f"${pnl:+.2f}"
                rows.append({
                    "": emoji,
                    "Market": str(r["market"])[:50],
                    "Outcome": r["outcome"],
                    "Trader(s)": r["trader"],
                    "Cost ($)": f"${float(r['total_cost']):.2f}",
                    "Avg Entry": (
                        f"${(float(r['total_cost']) / max(float(r['total_shares']), 0.0001)):.4f}"
                    ),
                    "Current": curr,
                    "Unreal PnL": pnl_str,
                    "Open For": r["time_open"],
                })
            display = pd.DataFrame(rows)
        else:
            pending = df[df["status"] == "PENDING"].copy()
            if pending.empty:
                st.success("No active positions — all trades resolved.")
                display = pd.DataFrame()
            else:
                agg = (
                    pending.groupby(
                        ["trader", "condition_id", "outcome_index", "market", "outcome"],
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
                    with st.spinner("Fetching current market prices..."):
                        curr_prices = [
                            fetch_price(row.condition_id, int(row.outcome_index))
                            for row in agg.itertuples()
                        ]
                    agg["current_px"] = curr_prices
                else:
                    agg["current_px"] = None
                _proj = agg["total_shares"] * agg["current_px"].fillna(agg["avg_entry"])
                _raw  = _proj - agg["total_cost"]
                agg["unreal_pnl"] = _raw.where(_raw <= 0, _raw * 0.98)
                agg["near_zero"] = agg["current_px"].notna() & (agg["current_px"] < 0.05)
                rows = []
                for _, r in agg.iterrows():
                    pnl = r["unreal_pnl"]
                    near_zero = bool(r.get("near_zero", False))
                    emoji = "⚠️" if near_zero else ("🟢" if pnl >= 0 else "🔴")
                    curr = f"${r['current_px']:.4f}" if pd.notna(r["current_px"]) else "N/A"
                    pnl_str = f"${pnl:+.2f} ⚠️ Near Zero" if near_zero else f"${pnl:+.2f}"
                    rows.append({
                        "": emoji,
                        "Market": r["market"][:50],
                        "Outcome": r["outcome"],
                        "Trader(s)": r["traders"],
                        "Cost ($)": f"${r['total_cost']:.2f}",
                        "Avg Entry": f"${r['avg_entry']:.4f}",
                        "Current": curr,
                        "Unreal PnL": pnl_str,
                        "Open For": r["time_open"],
                    })
                display = pd.DataFrame(rows)

        if display.empty:
            st.success("No active positions — all trades resolved.")
        else:
            def _color_row(row):
                pnl_cell = row.get("Unreal PnL", "$0")
                if "Near Zero" in pnl_cell:
                    return ["background-color:rgba(220,53,69,0.35)"] * len(row)
                try:
                    val = float(pnl_cell.split()[0].replace("$", "").replace("+", ""))
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
                width="stretch",
                hide_index=True,
            )
            st.caption(f"{len(display)} active position(s)")

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
            _csv_traders = sorted(df["trader"].dropna().unique().tolist())
            trader_filter = st.multiselect(
                "Trader", options=_csv_traders, default=_csv_traders, key="hist_trader"
            )
        with f2:
            status_filter = st.multiselect(
                "Outcome", options=["WIN", "LOSS", "PENDING"],
                default=["WIN", "LOSS", "PENDING"], key="hist_status"
            )

        mask     = df["trader"].isin(trader_filter) & df["status"].isin(status_filter)
        filtered = df[mask].sort_values("timestamp", ascending=False)

        f3, _ = st.columns([1, 3])
        with f3:
            page_size = st.selectbox(
                "Rows per page", [10, 25, 50, "All"], index=3, key="hist_page_size"
            )
        view = filtered if page_size == "All" else filtered.head(int(page_size))
        st.caption(
            f"Showing **{len(view)}** of **{len(filtered)}** filtered "
            f"({len(df)} total)"
        )

        if not filtered.empty:
            display = view[[
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
                width="stretch",
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

    _wl_active, _wl_inactive, _wl_last_refresh = load_watchlist_cache()
    if _wl_last_refresh != "never":
        st.caption(f"Watchlist last refreshed by dynamic_watchlist: **{_wl_last_refresh}**")
    else:
        st.warning(
            "Watchlist cache has no refresh timestamp — "
            "dynamic_watchlist.py may not have run yet."
        )

    # ── Watchlist cards (active top-N traders only) ───────────────────────────
    wl_traders = sorted(_wl_active.keys())
    if not wl_traders:
        st.info("No active traders in watchlist_cache.json — bot may not have run a refresh yet.")
    else:
        ncols = min(len(wl_traders), 3)
        cols  = st.columns(ncols)
        for i, trader in enumerate(wl_traders):
            tdf      = df[df["trader"] == trader].copy() if not df.empty else pd.DataFrame()
            resolved = positions_df(tdf) if not tdf.empty else pd.DataFrame()

            wins_t   = int((resolved["status"] == "WIN").sum())  if not resolved.empty else 0
            losses_t = int((resolved["status"] == "LOSS").sum()) if not resolved.empty else 0
            total_t  = wins_t + losses_t
            wr_t     = wins_t / total_t * 100 if total_t else 0.0
            pnl_t    = float(resolved["resolved_pnl"].sum()) if not resolved.empty else 0.0
            spent_t  = float(tdf["our_size_usdc"].sum())     if not tdf.empty     else 0.0

            week_trades   = len(tdf[tdf["timestamp"] >= week_ago]) if not tdf.empty else 0
            last_trade_dt = tdf["timestamp"].max()                  if not tdf.empty else None

            if last_trade_dt is not None and pd.notna(last_trade_dt):
                if getattr(last_trade_dt, "tzinfo", None) is None:
                    last_trade_dt = last_trade_dt.replace(tzinfo=timezone.utc)
                is_active      = (now_utc - last_trade_dt).total_seconds() < 604800
                last_trade_str = last_trade_dt.strftime("%Y-%m-%d %H:%M UTC")
            else:
                is_active      = False
                last_trade_str = "Never"

            badge_html = (
                '<span class="badge-active">● Active</span>'
                if is_active else
                '<span class="badge-inactive">○ Inactive</span>'
            )

            live = fetch_trader_live_stats(trader)

            with cols[i % ncols]:
                st.markdown(
                    f'<div class="trader-card"><b>👤 {trader}</b> &nbsp; {badge_html}</div>',
                    unsafe_allow_html=True,
                )

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
                    lc.metric("30d Rank",      rank_30d_str)
                    ld.metric("All-Time Rank", rank_all_str)
                else:
                    st.caption("⚠️ Not found on Polymarket leaderboard")

                m1, m2 = st.columns(2)
                m1.metric("Trades Copied", len(tdf))
                m2.metric("This Week",     week_trades)
                m3, m4 = st.columns(2)
                m3.metric("Our Win Rate",  f"{wr_t:.1f}%")
                m4.metric("Profit for Us", f"${pnl_t:+.2f}")

                st.caption(f"Last trade: **{last_trade_str}**")
                st.caption(f"{wins_t}W / {losses_t}L · ${spent_t:.2f} invested")

                if total_t > 0:
                    st.progress(wr_t / 100)

                if not resolved.empty:
                    chart = (
                        resolved.sort_values("timestamp")[["timestamp", "resolved_pnl"]]
                        .set_index("timestamp")
                        .rename(columns={"resolved_pnl": "Cumulative PnL ($)"})
                    )
                    chart["Cumulative PnL ($)"] = chart["Cumulative PnL ($)"].cumsum()
                    st.line_chart(chart, height=150)

    # ── Previously watched (inactive / archived traders) ─────────────────────
    if _wl_inactive:
        with st.expander(f"Previously watched ({len(_wl_inactive)} archived traders)"):
            st.caption(
                "These traders were previously in the top-N watchlist but have since "
                "been rotated out. Their addresses are kept so open positions can still resolve."
            )
            arch_rows = []
            for trader in sorted(_wl_inactive.keys()):
                tdf_a    = df[df["trader"] == trader] if not df.empty else pd.DataFrame()
                res_a    = positions_df(tdf_a) if not tdf_a.empty else pd.DataFrame()
                wins_a   = int((res_a["status"] == "WIN").sum())   if not res_a.empty  else 0
                losses_a = int((res_a["status"] == "LOSS").sum())  if not res_a.empty  else 0
                total_a  = wins_a + losses_a
                pnl_a    = float(res_a["resolved_pnl"].sum())      if not res_a.empty  else 0.0
                wr_a     = wins_a / total_a * 100                  if total_a          else 0.0
                pending_a = int((tdf_a["status"] == "PENDING").sum()) if not tdf_a.empty else 0
                arch_rows.append({
                    "Trader":        trader,
                    "Trades":        len(tdf_a),
                    "Open Positions": pending_a,
                    "W":             wins_a,
                    "L":             losses_a,
                    "Win Rate":      f"{wr_a:.1f}%",
                    "PnL ($)":       f"${pnl_a:+.2f}",
                })
            st.dataframe(pd.DataFrame(arch_rows), width="stretch", hide_index=True)

    # ── Other Active Traders (in CSV but not in watchlist cache at all) ───────
    if not df.empty:
        wl_set        = set(_wl_active.keys()) | set(_wl_inactive.keys())
        csv_traders   = set(df["trader"].dropna().unique())
        other_traders = sorted(csv_traders - wl_set)

        if other_traders:
            st.divider()
            st.markdown("#### Other Active Traders")
            st.caption(
                "These traders appear in paper_trades.csv but are not in the watchlist cache. "
                "Live Polymarket stats are not fetched for them."
            )

            other_rows = []
            for trader in other_traders:
                tdf      = df[df["trader"] == trader]
                resolved = positions_df(tdf)
                wins_o   = int((resolved["status"] == "WIN").sum())  if not resolved.empty else 0
                losses_o = int((resolved["status"] == "LOSS").sum()) if not resolved.empty else 0
                total_o  = wins_o + losses_o
                wr_o     = wins_o / total_o * 100 if total_o else 0.0
                pnl_o    = float(resolved["resolved_pnl"].sum()) if not resolved.empty else 0.0
                last_dt  = tdf["timestamp"].max() if not tdf.empty else None
                if last_dt is not None and pd.notna(last_dt):
                    if getattr(last_dt, "tzinfo", None) is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    last_str = last_dt.strftime("%Y-%m-%d %H:%M UTC")
                else:
                    last_str = "Never"
                other_rows.append({
                    "Trader":      trader,
                    "Trades":      len(tdf),
                    "W":           wins_o,
                    "L":           losses_o,
                    "Win Rate":    f"{wr_o:.1f}%",
                    "PnL ($)":     f"${pnl_o:+.2f}",
                    "Last Trade":  last_str,
                })

            st.dataframe(
                pd.DataFrame(other_rows),
                width="stretch",
                hide_index=True,
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — PERFORMANCE CHARTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_perf:
    st.subheader("Performance Charts")

    resolved_all = positions_df(df).copy() if not df.empty else pd.DataFrame()

    if resolved_all.empty:
        st.info("No resolved trades to chart yet.")
    else:
        resolved_all = resolved_all.sort_values("timestamp")

        # ── Shared dark-theme helpers ─────────────────────────────────────
        _AXIS_STYLE = dict(
            gridcolor="rgba(255,255,255,0.05)",
            linecolor="rgba(255,255,255,0.10)",
            tickcolor="rgba(255,255,255,0.20)",
            zerolinecolor="rgba(255,255,255,0.10)",
        )
        _BASE_LAYOUT = dict(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c9d1d9", size=12),
            title_font=dict(color="#8b949e", size=14),
            title_x=0,
            showlegend=False,
            margin=dict(t=44, b=44, l=60, r=24),
            hoverlabel=dict(
                bgcolor="#0d1117",
                bordercolor="#30363d",
                font_color="#c9d1d9",
            ),
        )

        def _apply_dark(fig, **extra_layout):
            fig.update_layout(**_BASE_LAYOUT, **extra_layout)
            fig.update_xaxes(**_AXIS_STYLE)
            fig.update_yaxes(**_AXIS_STYLE)

        # ── Summary stats row ─────────────────────────────────────────────
        total_return_pct = (
            stats["all_time_pnl"] / STARTING_BANKROLL * 100
            if STARTING_BANKROLL else 0.0
        )
        resolved_all["trade_date"] = resolved_all["timestamp"].dt.date
        _day_pnl = resolved_all.groupby("trade_date")["resolved_pnl"].sum()
        best_day_pnl  = float(_day_pnl.max()) if not _day_pnl.empty else 0.0
        worst_day_pnl = float(_day_pnl.min()) if not _day_pnl.empty else 0.0
        streak_label  = (
            f"{stats['streak_count']} {stats['streak_type']}"
            if stats["streak_count"] else "—"
        )

        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Total Return",  f"{total_return_pct:+.1f}%")
        sm2.metric("Best Day PnL",  f"${best_day_pnl:+.2f}")
        sm3.metric("Worst Day PnL", f"${worst_day_pnl:+.2f}")
        sm4.metric("Win Streak",    streak_label)

        st.divider()

        # ── Chart 1: Cumulative PnL with area fill ────────────────────────
        resolved_all["cum_pnl"] = resolved_all["resolved_pnl"].cumsum()
        fig_pnl = px.line(
            resolved_all,
            x="timestamp",
            y="cum_pnl",
            title="Cumulative PnL Over Time",
            labels={"timestamp": "Date", "cum_pnl": "Cumulative PnL ($)"},
            color_discrete_sequence=["#00C48C"],
            line_shape="spline",
        )
        fig_pnl.update_traces(
            fill="tozeroy",
            fillcolor="rgba(0,196,140,0.08)",
            line=dict(width=2),
        )
        fig_pnl.add_hline(
            y=0,
            line_dash="dash",
            line_color="rgba(255,255,255,0.15)",
            opacity=1,
        )
        fig_pnl.add_hline(
            y=STARTING_BANKROLL,
            line_dash="dot",
            line_color="rgba(255,200,60,0.45)",
            opacity=1,
            annotation_text="Starting Capital",
            annotation_font_color="#8b949e",
            annotation_position="bottom right",
        )
        _apply_dark(fig_pnl, hovermode="x unified")
        st.plotly_chart(fig_pnl, width="stretch")

        # ── Chart 2: Rolling 10-trade win rate ────────────────────────────
        resolved_all["is_win"]  = (resolved_all["status"] == "WIN").astype(int)
        resolved_all["roll_wr"] = resolved_all["is_win"].rolling(10, min_periods=1).mean() * 100
        fig_wr = px.line(
            resolved_all,
            x="timestamp",
            y="roll_wr",
            title="Win Rate Trend (Rolling 10-Trade Average)",
            labels={"timestamp": "Date", "roll_wr": "Win Rate (%)"},
            color_discrete_sequence=["#4A90D9"],
            line_shape="spline",
        )
        fig_wr.update_traces(line=dict(width=2))
        fig_wr.add_hline(
            y=MIN_WIN_RATE,
            line_dash="dash",
            line_color="rgba(255,220,60,0.50)",
            opacity=1,
            annotation_text=f"{MIN_WIN_RATE:.0f}% target",
            annotation_font_color="#8b949e",
            annotation_position="bottom right",
        )
        _apply_dark(fig_wr, hovermode="x unified", yaxis=dict(**_AXIS_STYLE, range=[0, 100]))
        st.plotly_chart(fig_wr, width="stretch")

        # ── Chart 3: PnL by Trader (horizontal bar) ───────────────────────
        # Dedup per (trader, position) so shared positions count for each trader
        _trader_pos = (
            df[df["status"].isin(["WIN", "LOSS"])]
            .sort_values("timestamp")
            .groupby(["trader", "condition_id", "outcome_index"], as_index=False)
            .first()
        ) if not df.empty else pd.DataFrame()
        _tpnl = (
            _trader_pos.groupby("trader")["resolved_pnl"]
            .sum()
            .reset_index()
            .sort_values("resolved_pnl")
        ) if not _trader_pos.empty else pd.DataFrame(columns=["trader", "resolved_pnl"])
        _tpnl["color"] = _tpnl["resolved_pnl"].apply(
            lambda v: "#00C48C" if v >= 0 else "#dc3545"
        )
        fig_tpnl = go.Figure(go.Bar(
            y=_tpnl["trader"],
            x=_tpnl["resolved_pnl"],
            orientation="h",
            marker_color=_tpnl["color"].tolist(),
            text=_tpnl["resolved_pnl"].map("${:+.2f}".format),
            textposition="outside",
            textfont=dict(color="#c9d1d9", size=11),
            cliponaxis=False,
        ))
        fig_tpnl.add_vline(x=0, line_color="rgba(255,255,255,0.15)")
        _apply_dark(
            fig_tpnl,
            title="PnL by Trader",
            xaxis=dict(**_AXIS_STYLE, title="Total Resolved PnL ($)"),
            yaxis=dict(**_AXIS_STYLE, title=""),
            hovermode="y unified",
        )
        st.plotly_chart(fig_tpnl, width="stretch")

        # ── Chart 4: Win Rate by Trader (horizontal bar) ──────────────────
        _twr_rows = []
        for _trader, _grp in _trader_pos.groupby("trader"):
            _wins  = int((_grp["status"] == "WIN").sum())
            _total = len(_grp)
            _twr_rows.append({
                "trader":       _trader,
                "win_rate":     _wins / _total * 100 if _total else 0.0,
                "trades":       _total,
            })
        _twr = pd.DataFrame(_twr_rows).sort_values("win_rate")
        _twr["color"] = _twr["win_rate"].apply(
            lambda v: "#00C48C" if v >= MIN_WIN_RATE else "#dc3545"
        )
        fig_twr = go.Figure(go.Bar(
            y=_twr["trader"],
            x=_twr["win_rate"],
            orientation="h",
            marker_color=_twr["color"].tolist(),
            text=_twr["win_rate"].map("{:.1f}%".format),
            textposition="outside",
            textfont=dict(color="#c9d1d9", size=11),
            cliponaxis=False,
            customdata=_twr["trades"].tolist(),
            hovertemplate="%{y}: %{x:.1f}% win rate (%{customdata} trades)<extra></extra>",
        ))
        fig_twr.add_vline(
            x=MIN_WIN_RATE,
            line_dash="dash",
            line_color="rgba(255,220,60,0.50)",
            annotation_text=f"{MIN_WIN_RATE:.0f}% target",
            annotation_font_color="#8b949e",
            annotation_position="top right",
        )
        _apply_dark(
            fig_twr,
            title="Win Rate by Trader",
            xaxis=dict(**_AXIS_STYLE, title="Win Rate (%)", range=[0, 115]),
            yaxis=dict(**_AXIS_STYLE, title=""),
            hovermode="y unified",
        )
        st.plotly_chart(fig_twr, width="stretch")

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
            st.plotly_chart(fig_pie, width="stretch")

        with table_col:
            resolved_cat = positions_df(df_cat)
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
                st.dataframe(cat_stats_df, width="stretch", hide_index=True)

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

    resolved_risk = positions_df(df).copy() if not df.empty else pd.DataFrame()

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
        st.markdown("**Daily Net Loss**")
        gross_wins   = stats["today_gross_wins"]  or 0.0
        gross_losses = stats["today_gross_losses"] or 0.0
        net_loss     = gross_losses - gross_wins
        cap_remaining = max(0.0, DAILY_LOSS_CAP - net_loss)

        d1, d2 = st.columns(2)
        d1.metric("Today's Losses",  f"${gross_losses:.2f}")
        d2.metric("Today's Wins",    f"${gross_wins:.2f}")
        loss_pct = min(max(net_loss, 0.0) / DAILY_LOSS_CAP, 1.0) if DAILY_LOSS_CAP else 0.0
        st.progress(loss_pct)
        st.caption(
            f"Net loss ${net_loss:.2f} · ${cap_remaining:.2f} headroom · "
            f"${DAILY_LOSS_CAP:.0f} daily loss cap"
        )

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
            st.plotly_chart(fig_hist, width="stretch")
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
        if st.button("🔄 Refresh", width="stretch"):
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
