"""
Polymarket Copy Trading Bot — Streamlit Dashboard
=================================================
Run on VPS:
  streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

Set .env variables before running (see .env.example).
"""
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
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
TRADERS   = ["majorexploiter", "beachboy4"]

CSV_FIELDS = [
    "timestamp","trader","market","outcome","whale_side",
    "whale_size_usdc","our_size_usdc","price","copy_shares",
    "status","resolved_pnl","condition_id","outcome_index",
]

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polymarket Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS — mobile-friendly + dark accents ──────────────────────────────────────
st.markdown("""
<style>
  /* Mobile padding */
  @media (max-width: 768px) {
    .block-container { padding: 0.5rem 0.5rem 3rem !important; }
    div[data-testid="column"] { padding: 2px !important; }
  }
  /* Mode badge */
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
  /* Win/loss row colors */
  .row-win  { background-color: rgba(0,166,81,0.15) !important; }
  .row-loss { background-color: rgba(220,53,69,0.15) !important; }
  .row-pend { background-color: rgba(255,193,7,0.10) !important; }
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


def bot_status() -> tuple[str, str]:
    """Returns (status, last_seen_str) from log file mtime."""
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


def compute_stats(df: pd.DataFrame) -> dict:
    resolved = df[df["status"].isin(["WIN", "LOSS"])]
    wins   = int((resolved["status"] == "WIN").sum())
    losses = int((resolved["status"] == "LOSS").sum())
    total  = wins + losses
    win_rate = wins / total * 100 if total else 0.0

    today_df = df[df["timestamp"].dt.date == date.today()] if not df.empty else df
    today_resolved = today_df[today_df["status"].isin(["WIN", "LOSS"])]

    return {
        "wins":          wins,
        "losses":        losses,
        "win_rate":      win_rate,
        "all_time_pnl":  float(resolved["resolved_pnl"].sum()),
        "today_pnl":     float(today_resolved["resolved_pnl"].sum()),
        "today_spent":   float(today_df["our_size_usdc"].sum()),
        "total_trades":  len(df),
    }


def fmt_duration(td) -> str:
    s = int(td.total_seconds())
    h, m = divmod(s, 3600)
    m //= 60
    return f"{h}h {m}m" if h else f"{m}m"


# ── Load data ─────────────────────────────────────────────────────────────────
df = load_trades()
stats = compute_stats(df) if not df.empty else {
    "wins":0,"losses":0,"win_rate":0,"all_time_pnl":0,
    "today_pnl":0,"today_spent":0,"total_trades":0,
}

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_ov, tab_pos, tab_hist, tab_traders, tab_logs = st.tabs([
    "📊 Overview",
    "💼 Positions",
    "📜 Trade History",
    "👥 Trader Stats",
    "🔍 Bot Logs",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tab_ov:
    # Mode badge
    if BOT_MODE.upper() == "LIVE":
        st.markdown('<div class="badge-wrap"><span class="badge-live">🔴 LIVE TRADING</span></div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="badge-wrap"><span class="badge-paper">🟠 PAPER TRADING — Simulated</span></div>',
                    unsafe_allow_html=True)

    # PnL row
    c1, c2, c3 = st.columns(3)
    c1.metric("Today's PnL",   f"${stats['today_pnl']:+.2f}")
    c2.metric("All-Time PnL",  f"${stats['all_time_pnl']:+.2f}")
    c3.metric("Total Trades",  stats["total_trades"])

    st.divider()

    # Win rate
    st.subheader("Win Rate")
    wr_col, wr_lbl = st.columns([4, 1])
    with wr_col:
        st.progress(min(stats["win_rate"] / 100, 1.0))
    with wr_lbl:
        st.write(f"**{stats['win_rate']:.1f}%**")
    st.caption(f"{stats['wins']} wins / {stats['losses']} losses")

    st.divider()

    # Daily budget
    st.subheader("Daily Budget")
    today_spent    = stats["today_spent"] or 0.0
    today_remaining = max(0.0, DAILY_BUDGET - today_spent)
    budget_pct     = min(today_spent / DAILY_BUDGET, 1.0) if DAILY_BUDGET else 0.0

    b_col, b_lbl = st.columns([4, 1])
    with b_col:
        st.progress(budget_pct)
    with b_lbl:
        st.write(f"Used **${today_spent:.2f}**")
    st.caption(f"${today_remaining:.2f} remaining of ${DAILY_BUDGET:.0f} daily budget")

    st.divider()

    # Bot status
    st.subheader("Bot Status")
    status_str, last_seen = bot_status()
    icon = {"Online": "🟢", "Idle": "🟡", "Offline": "🔴"}.get(status_str, "⚪")
    s_col, s_lbl = st.columns(2)
    with s_col:
        st.markdown(f"### {icon} {status_str}")
    with s_lbl:
        st.write(f"Last heartbeat:")
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
            # Aggregate multiple copy-ins on the same market into one position
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
            now_utc = datetime.now(timezone.utc)
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

            # Build display table
            rows = []
            for _, r in agg.iterrows():
                pnl = r["unreal_pnl"]
                emoji = "🟢" if pnl >= 0 else "🔴"
                curr = f"${r['current_px']:.4f}" if pd.notna(r["current_px"]) else "N/A"
                rows.append({
                    "": emoji,
                    "Market":      r["market"][:50],
                    "Outcome":     r["outcome"],
                    "Trader(s)":   r["traders"],
                    "Cost ($)":    f"${r['total_cost']:.2f}",
                    "Avg Entry":   f"${r['avg_entry']:.4f}",
                    "Current":     curr,
                    "Unreal PnL":  f"${pnl:+.2f}",
                    "Open For":    r["time_open"],
                })

            display = pd.DataFrame(rows)

            def _color_row(row):
                pnl_str = row.get("Unreal PnL", "$0.00")
                try:
                    val = float(pnl_str.replace("$", "").replace("+", ""))
                except ValueError:
                    val = 0
                color = "rgba(0,166,81,0.15)" if val >= 0 else "rgba(220,53,69,0.15)"
                return [f"background-color:{color}"] * len(row)

            st.dataframe(
                display.style.apply(_color_row, axis=1),
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

        mask = df["trader"].isin(trader_filter) & df["status"].isin(status_filter)
        filtered = df[mask].sort_values("timestamp", ascending=False)

        st.caption(f"Showing **{len(filtered)}** of **{len(df)}** trades")

        if not filtered.empty:
            display = filtered[[
                "timestamp","trader","market","outcome",
                "whale_size_usdc","our_size_usdc","price","status","resolved_pnl",
            ]].copy()
            display["timestamp"]       = display["timestamp"].dt.strftime("%m-%d %H:%M")
            display["whale_size_usdc"] = display["whale_size_usdc"].map("${:,.0f}".format)
            display["our_size_usdc"]   = display["our_size_usdc"].map("${:.2f}".format)
            display["price"]           = display["price"].map("${:.4f}".format)
            display["resolved_pnl"]    = display["resolved_pnl"].apply(
                lambda x: f"${x:+.4f}" if pd.notna(x) and x != "" else ""
            )
            display.columns = [
                "Time","Trader","Market","Outcome",
                "Whale ($)","Our ($)","Price","Status","PnL ($)",
            ]

            def _color_status(row):
                s = row["Status"]
                if s == "WIN":     c = "rgba(0,166,81,0.20)"
                elif s == "LOSS":  c = "rgba(220,53,69,0.20)"
                else:              c = "rgba(255,193,7,0.12)"
                return [f"background-color:{c}"] * len(row)

            st.dataframe(
                display.style.apply(_color_status, axis=1),
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
# TAB 4 — TRADER STATS
# ══════════════════════════════════════════════════════════════════════════════
with tab_traders:
    st.subheader("Trader Comparison")

    if df.empty:
        st.info("No trades recorded yet.")
    else:
        cols = st.columns(len(TRADERS))
        for i, trader in enumerate(TRADERS):
            tdf      = df[df["trader"] == trader]
            resolved = tdf[tdf["status"].isin(["WIN", "LOSS"])]
            wins_t   = int((resolved["status"] == "WIN").sum())
            losses_t = int((resolved["status"] == "LOSS").sum())
            total_t  = wins_t + losses_t
            wr_t     = wins_t / total_t * 100 if total_t else 0.0
            pnl_t    = float(resolved["resolved_pnl"].sum())
            spent_t  = float(tdf["our_size_usdc"].sum())

            with cols[i]:
                st.markdown(f"### 👤 {trader}")
                st.metric("Trades Copied", len(tdf))
                st.metric("Win Rate",      f"{wr_t:.1f}%")
                st.metric("PnL",           f"${pnl_t:+.2f}")
                st.metric("Total Invested",f"${spent_t:.2f}")

                if total_t > 0:
                    st.progress(wr_t / 100)
                    st.caption(f"{wins_t}W / {losses_t}L")

                # Cumulative PnL chart
                if not resolved.empty:
                    chart = (
                        resolved.sort_values("timestamp")[["timestamp","resolved_pnl"]]
                        .set_index("timestamp")
                        .rename(columns={"resolved_pnl": "Cumulative PnL ($)"})
                    )
                    chart["Cumulative PnL ($)"] = chart["Cumulative PnL ($)"].cumsum()
                    st.line_chart(chart, height=180)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — BOT LOGS
# ══════════════════════════════════════════════════════════════════════════════
with tab_logs:
    hdr, btn_col = st.columns([5, 1])
    hdr.subheader("Bot Logs")
    with btn_col:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if not LOG_PATH.exists():
        st.warning(f"Log file not found.")
        st.code(str(LOG_PATH.resolve()), language=None)
        st.info("Set `LOG_PATH` in `.env` to point to your bot.log file.")
    else:
        try:
            text  = LOG_PATH.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            last50 = "\n".join(lines[-50:])
            st.code(last50, language=None)
            st.caption(
                f"Last {min(50, len(lines))} of {len(lines)} lines · "
                f"`{LOG_PATH.resolve()}`"
            )
        except Exception as e:
            st.error(f"Error reading log file: {e}")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    f"🕐 Last loaded: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
    f"· Auto-refreshes every {REFRESH_MS // 1000}s"
)
