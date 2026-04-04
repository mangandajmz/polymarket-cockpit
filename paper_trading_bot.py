from __future__ import annotations

"""
Polymarket Paper Trading Bot
============================
[PAPER MODE] — Simulated trades only. No real money. No wallet connected.

Watchlist: dynamic — top 5 monthly-PNL traders with min 60% win rate
Ratio:     10:1  |  Daily budget: $50  |  Min whale: $150
Poll:      every 30 seconds
"""

import csv, hashlib, json, os, re, subprocess, sys, time, threading, statistics
from datetime import datetime, date, timezone
from pathlib import Path

import requests
from api_client import JsonApiClient
from dynamic_watchlist import WatchlistManager, TRADER_BLOCKLIST
from state_store import StateStore

try:
    from rich.live import Live
    from rich.table import Table
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────
DATA_API         = "https://data-api.polymarket.com/v1"
GAMMA_API        = "https://gamma-api.polymarket.com"
CLOB_API         = "https://clob.polymarket.com"
COPY_RATIO             = 0.10    # 1/10th of whale trade size (legacy, not used for sizing)
DAILY_LOSS_CAP         = 60.0   # max net loss (losses minus wins) per calendar day before halting new trades
BASE_BET               = 10.0   # base bet size in USD
MAX_BET                = 30.0   # maximum bet size per trade in USD
MAX_ENTRY_PRICE        = 0.75   # skip near-certainty bets — poor R/R above this price
MAX_DEPLOY_PCT         = 0.60   # never deploy more than 60% of bankroll simultaneously
STARTING_BANKROLL      = 300.0  # starting simulated bankroll in USD
MIN_WHALE_SIZE         = 1000.0 # minimum whale USDC size to copy
MAX_TRADE_AGE          = 300    # 5 minutes in seconds
POLL_INTERVAL          = 30     # seconds between wallet polls
RESOLVE_INTERVAL       = 60     # seconds between resolution checks
ADDRESS_REFRESH_HOURS  = 6      # how often to re-resolve trader addresses (legacy mode)
MIN_TRADES_FOR_CUTOFF        = 10  # min resolved trades before applying win-rate gate
MIN_WIN_RATE                 = 60.0  # % — stop copying a trader below this threshold (matches watchlist qualification threshold)
MAX_DAILY_LOSSES_PER_TRADER  = 2   # max losses from one trader per calendar day before skipping
MAX_DAILY_DEPLOY_PER_TRADER  = 60.0  # max $ deployed to one trader per day
STALE_POSITION_DAYS    = 30     # flag positions open longer than this
ZERO_PRICE_CLOSE_HOURS = 24     # force-close unresolved position if price ≈$0 longer than this
ZERO_PRICE_GUARD       = 0.005  # treat anything below this as effectively zero
MAX_OPEN_HOURS         = 72     # force-close any unresolved position open longer than this
MAX_API_FAILURES       = 5      # consecutive poll failures before status warning
SEEN_HASHES_FILE       = "seen_hashes.json"
INTERACTIVE_MODE       = False  # True only when running manually in terminal

# ── Dynamic watchlist config ───────────────────────────────────────────────────
USE_DYNAMIC_WATCHLIST     = True   # set False to revert to static TRADERS_TO_COPY
WATCHLIST_TOP_N           = 5      # traders to watch (top N by monthly PNL after WR filter)
WATCHLIST_MIN_WR          = 60.0   # % — minimum win rate to enter the dynamic watchlist
WATCHLIST_REFRESH_H       = 6      # hours between dynamic watchlist refreshes

# Phase 2 threshold: when simulated bankroll exceeds $500, consider upgrading to
# WATCHLIST_TOP_N=10, WATCHLIST_MIN_WR=40.0, MIN_WHALE_SIZE=100.0 for more
# trade signals. The current Phase 1 settings are conservative for a small bankroll.
PHASE2_BANKROLL_THRESHOLD = 500.0

# Bankroll scaling thresholds: (min_bankroll, {BASE_BET, MAX_BET, DAILY_LOSS_CAP})
BANKROLL_SCALE_STEPS = [
    (150,  {"BASE_BET":  5, "MAX_BET":  15, "DAILY_LOSS_CAP":  30}),
    (300,  {"BASE_BET": 10, "MAX_BET":  30, "DAILY_LOSS_CAP":  60}),
    (500,  {"BASE_BET": 15, "MAX_BET":  50, "DAILY_LOSS_CAP": 100}),
    (1000, {"BASE_BET": 25, "MAX_BET": 100, "DAILY_LOSS_CAP": 200}),
    (2500, {"BASE_BET": 40, "MAX_BET": 150, "DAILY_LOSS_CAP": 300}),
]

# Legacy static list — only used when USE_DYNAMIC_WATCHLIST = False
TRADERS_TO_COPY  = ["majorexploiter", "beachboy4"]
CSV_FILE         = Path(os.getenv("CSV_PATH", str(Path(__file__).parent / "paper_trades.csv")))
STATE_DB_FILE    = Path(os.getenv("STATE_DB_PATH", str(Path(__file__).parent / "bot_state.db")))

CRYPTO_KW = {
    "bitcoin","btc","ethereum","eth","crypto","solana","sol","xrp",
    "ripple","dogecoin","doge","bnb","binance","nft","blockchain",
    "defi","polygon","matic","avalanche","avax","chainlink","cardano",
    "ada","litecoin","ltc","usdc","usdt","stablecoin","web3","token",
}

FUTURES_KW = {
    "nba finals", "nhl finals", "world series", "super bowl",
    "win the", "qualify for", "advance to", "2026 champion", "2027 champion"
}

CSV_FIELDS = [
    "timestamp","trader","market","outcome","whale_side",
    "whale_size_usdc","our_size_usdc","price","copy_shares",
    "conviction","status","resolved_pnl","condition_id","outcome_index",
    "event_id","position_id",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> date:
    return utc_now().date()


def utc_today_str() -> str:
    return utc_today().isoformat()


def make_position_id(trader_name: str, condition_id: str, outcome_index: int) -> str:
    return f"{trader_name}|{condition_id}|{outcome_index}"


def make_trade_event_id(trader_name: str, trade: dict) -> str:
    raw = "|".join([
        trader_name,
        str(trade.get("transactionHash", "")),
        str(trade.get("conditionId", "")),
        str(trade.get("outcomeIndex", 0)),
        str(trade.get("side", "")),
        str(trade.get("timestamp", 0)),
        str(trade.get("usdcSize") or trade.get("size") or 0),
        str(trade.get("price", 0)),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_build_version() -> str:
    env_version = os.getenv("APP_BUILD_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


HTTP = JsonApiClient(default_timeout=10.0, default_retries=3, backoff_base=1.0, jitter_max=0.2)

# ── Bot State ─────────────────────────────────────────────────────────────────
class PaperBot:
    def __init__(self):
        self.lock               = threading.Lock()
        self.store              = StateStore(STATE_DB_FILE)
        self.trader_addrs       = {}        # name → proxyWallet address
        self.seen_hashes        = set()     # processed event ids
        self.positions          = {}        # (trader, cond_id, oidx) → position dict
        self.trade_log          = []        # list of trade record dicts
        self.daily_losses       = 0.0      # gross losses today (sum of abs negative pnl)
        self.daily_wins         = 0.0      # gross wins today (sum of positive pnl)
        self._budget_date       = utc_today()
        self.closed_pnl         = 0.0      # realised PnL only
        self.wins               = 0
        self.losses             = 0
        self.status_msg         = "Starting up..."
        self.last_poll          = "Never"
        self.trader_stats       = {}        # name → {"wins": int, "losses": int}
        self.api_fail_count     = 0         # consecutive poll cycles with all-None responses
        self.last_addr_refresh  = time.time()
        self.whale_sizes             = []   # rolling last-30 whale trade sizes for median
        self.daily_losses_per_trader = {}  # trader_name → losses today (reset at midnight UTC)
        self.daily_deploy_per_trader = {}  # trader → $ deployed today
        self.milestones_reached      = set()  # bankroll thresholds already prompted

    def _refresh_budget(self):
        today = utc_today()
        if today != self._budget_date:
            self.daily_losses            = 0.0
            self.daily_wins              = 0.0
            self._budget_date            = today
            self.daily_losses_per_trader = {}
            self.daily_deploy_per_trader = {}
        self.store.set_daily_risk(today.isoformat(), self.daily_wins, self.daily_losses)
        self.store.set_value("budget_day_utc", today.isoformat())

    @property
    def daily_net_loss(self):
        """Net loss today = gross losses - gross wins. Positive means we're down money."""
        return self.daily_losses - self.daily_wins

    @property
    def win_rate(self):
        total = self.wins + self.losses
        return self.wins / total * 100 if total else 0.0

    @property
    def open_positions(self):
        return [p for p in self.positions.values() if p["status"] == "OPEN"]

    @property
    def unrealised_pnl(self):
        return sum(p["pnl"] for p in self.open_positions)

    @property
    def total_pnl(self):
        return self.closed_pnl + self.unrealised_pnl


# ── API Helpers ───────────────────────────────────────────────────────────────
def get(url, params=None, timeout=10, retries=3):
    return HTTP.get_json(url, params=params, timeout=timeout, retries=retries)


# ── Startup ───────────────────────────────────────────────────────────────────
def resolve_addresses():
    """Fetch leaderboard, return {name: proxyWallet} for target traders."""
    data = get(f"{DATA_API}/leaderboard", {
        "timePeriod": "MONTH", "orderBy": "PNL", "limit": 50
    })
    if not data:
        return {}
    rows = data if isinstance(data, list) else data.get("data", [])
    out = {}
    for row in rows:
        name = row.get("name") or row.get("userName") or ""
        if name in TRADERS_TO_COPY:
            out[name] = row.get("proxyWallet", "")
    return out


def address_refresh_loop(bot: PaperBot):
    """Periodically re-resolve trader wallet addresses from the leaderboard."""
    interval = ADDRESS_REFRESH_HOURS * 3600
    while True:
        time.sleep(interval)
        fresh = resolve_addresses()
        if fresh:
            with bot.lock:
                bot.trader_addrs = fresh
                bot.last_addr_refresh = time.time()


def seed_seen_hashes(bot: PaperBot):
    """Index recent trades so we don't fire on old data at startup."""
    bot.seen_hashes.update(bot.store.load_seen_events())
    cutoff = int(time.time()) - 3600
    for name, addr in bot.trader_addrs.items():
        data = get(f"{DATA_API}/trades", {
            "user": addr, "limit": 100, "offset": 0, "takerOnly": "false"
        })
        if data and isinstance(data, list):
            for t in data:
                if int(t.get("timestamp", 0)) >= cutoff:
                    bot.seen_hashes.add(make_trade_event_id(name, t))
        time.sleep(0.3)

    # Merge persisted hashes from previous sessions
    if os.path.exists(SEEN_HASHES_FILE):
        try:
            with open(SEEN_HASHES_FILE, "r") as f:
                persisted = set(json.load(f))
            bot.seen_hashes.update(persisted)
            _log(f"Loaded {len(persisted)} persisted hashes from disk")
        except Exception as e:
            _log(f"Could not load seen_hashes.json — starting fresh: {e}")


# ── CSV ───────────────────────────────────────────────────────────────────────
def migrate_csv():
    """
    Migrate CSV to the current CSV_FIELDS schema if the header is out of date.

    Background: when new columns are added to CSV_FIELDS (e.g. 'conviction'),
    init_csv() never rewrites an existing header.  Rows written before the
    schema change have fewer fields than rows written after, which causes pandas
    to throw 'ParserError: Expected N fields, saw M' and crash the dashboard.

    This function detects a stale header and rewrites the entire file with the
    current CSV_FIELDS header, inserting empty strings for any missing columns
    in old rows.  Safe to call on every startup — exits immediately if the
    header is already current.
    """
    if not CSV_FILE.exists():
        return
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f)
            old_fields = reader.fieldnames or []
            if set(old_fields) == set(CSV_FIELDS) and old_fields == CSV_FIELDS:
                return   # header already matches — nothing to do
            rows = list(reader)

        # Rewrite with current header; old rows get empty strings for new columns
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})

        _log(
            f"migrate_csv: rewrote {len(rows)} rows "
            f"(old fields: {old_fields} → new: {CSV_FIELDS})"
        )
        print(f"  [migrate_csv] Updated CSV schema: added missing columns, {len(rows)} rows preserved.")
    except Exception as exc:
        print(f"  [migrate_csv] WARNING: migration failed: {exc}")


def init_csv():
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def _parse_ts(ts_str: str) -> float:
    """Parse a CSV timestamp string to a UTC unix timestamp.
    Tries multiple formats; falls back to 48h ago so force-close fires promptly."""
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        except ValueError:
            continue
    # Last resort: pandas
    try:
        import pandas as _pd
        return _pd.to_datetime(ts_str, utc=True).timestamp()
    except Exception:
        _log(f"  [WARN] Could not parse timestamp '{ts_str}' — defaulting to 48h ago")
        return time.time() - (48 * 3600)  # assume at least 48h old, not 0


def load_positions_from_csv(bot: PaperBot):
    """Reload open positions, closed PnL, today's net loss state, and whale sizes from CSV on restart."""
    if not CSV_FILE.exists():
        return
    today_prefix  = utc_today_str()   # "YYYY-MM-DD" — matches CSV timestamp prefix
    whale_records = []  # (timestamp_str, whale_size_usdc) for restoring conviction median
    # resolved_pnl is stamped identically on every row that shares a (cid, oidx) position.
    # Without deduplication, each multi-row position inflates closed_pnl and win/loss counts.
    seen_resolved = set()  # (cid, oidx) — count each resolved position only once
    seen_today    = set()  # (cid, oidx) — same dedup for today's daily budget restoration
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                status  = row.get("status")
                cid     = row.get("condition_id", "")
                oidx    = int(row.get("outcome_index", 0))
                trader  = row.get("trader", "unknown")
                pos_key = (trader, cid, oidx)
                record = dict(row)
                record["position_id"] = record.get("position_id") or make_position_id(trader, cid, oidx)
                record["event_id"] = record.get("event_id") or hashlib.sha256(
                    f"{trader}|{cid}|{oidx}|{row.get('timestamp','')}|{row.get('our_size_usdc','')}".encode("utf-8")
                ).hexdigest()
                bot.trade_log.append(record)

                # Restore today's daily_losses and daily_wins — one per position
                if row.get("timestamp", "").startswith(today_prefix) and status in ("WIN", "LOSS"):
                    if pos_key not in seen_today:
                        seen_today.add(pos_key)
                        pnl = float(row.get("resolved_pnl", 0) or 0)
                        if status == "WIN":
                            bot.daily_wins += pnl
                        else:
                            bot.daily_losses += abs(pnl)
                            # Restore per-trader loss count so the 3-loss rule
                            # survives bot restarts. Without this, daily_losses_per_trader
                            # resets to zero on every restart, allowing a blocked trader
                            # to keep firing trades after a restart.
                            if trader:
                                bot.daily_losses_per_trader[trader] = (
                                    bot.daily_losses_per_trader.get(trader, 0) + 1
                                )

                # Restore today's per-trader deploy spend — every row (not per-position dedup)
                # so that the daily dollar cap survives bot restarts.
                if row.get("timestamp", "").startswith(today_prefix):
                    _trader = row.get("trader", "")
                    _cost   = float(row.get("our_size_usdc", 0) or 0)
                    if _trader and _cost > 0:
                        bot.daily_deploy_per_trader[_trader] = (
                            bot.daily_deploy_per_trader.get(_trader, 0) + _cost
                        )

                # Accumulate whale sizes across all rows so conviction median is
                # warm on restart rather than starting cold from a single trade.
                ws = float(row.get("whale_size_usdc", 0) or 0)
                if ws > 0:
                    whale_records.append((row.get("timestamp", ""), ws))

                # Restore closed PnL and win/loss counts — one per position
                if status in ("WIN", "LOSS"):
                    if pos_key not in seen_resolved:
                        seen_resolved.add(pos_key)
                        pnl    = float(row.get("resolved_pnl", 0) or 0)
                        bot.closed_pnl += pnl
                        if pnl >= 0:
                            bot.wins += 1
                        else:
                            bot.losses += 1
                        # Restore all-time trader_stats from full CSV history.
                        # This makes win-rate gate, auto-drop, and perf_mult
                        # all work correctly from the first trade after any restart.
                        if trader:
                            s = bot.trader_stats.setdefault(
                                trader, {"wins": 0, "losses": 0}
                            )
                            if pnl >= 0:
                                s["wins"] += 1
                            else:
                                s["losses"] += 1
                if status not in ("PENDING", "OPEN"):
                    continue
                if not cid:
                    continue
                cost    = float(row.get("our_size_usdc", 0) or 0)
                price   = float(row.get("price", 0.001) or 0.001)
                shares  = float(row.get("copy_shares", 0) or 0)
                if pos_key not in bot.positions:
                    ts_str    = row.get("timestamp", "")
                    opened_at = _parse_ts(ts_str)
                    bot.positions[pos_key] = {
                        "position_id":   row.get("position_id") or make_position_id(trader, cid, oidx),
                        "condition_id":  cid,
                        "outcome_index": oidx,
                        "title":         row.get("market", cid[:30]),
                        "outcome":       row.get("outcome", str(oidx)),
                        "trader":        trader,
                        "opened_at":     opened_at,
                        "opened_at_utc": row.get("timestamp", ""),
                        "total_cost":    0.0,
                        "total_shares":  0.0,
                        "status":        "OPEN",
                        "pnl":           0.0,
                        "last_price":    price,
                    }
                pos = bot.positions[pos_key]
                pos["total_cost"]   += cost
                pos["total_shares"] += shares
    except Exception:
        pass

    # Restore rolling whale-size window — sort by timestamp, keep most recent 30.
    # Must be outside the try block so partial CSV reads still warm the median.
    if whale_records:
        whale_records.sort(key=lambda x: x[0])
        bot.whale_sizes = [w for _, w in whale_records[-30:]]


def load_positions_from_store(bot: PaperBot) -> bool:
    state = bot.store.load_runtime_state()
    if not state["positions"] and not state["fills"]:
        return False

    bot.closed_pnl = float(state["closed_pnl"])
    bot.wins = int(state["wins"])
    bot.losses = int(state["losses"])
    bot.trader_stats = dict(state["trader_stats"])
    bot.daily_losses_per_trader = dict(state["daily_losses_per_trader"])
    bot.daily_deploy_per_trader = dict(state["daily_deploy_per_trader"])
    bot.milestones_reached = set(state["milestones_reached"])
    bot.whale_sizes = list(state["whale_sizes"])
    budget_day = state.get("budget_day_utc") or utc_today_str()
    try:
        bot._budget_date = datetime.strptime(budget_day, "%Y-%m-%d").date()
    except ValueError:
        bot._budget_date = utc_today()
    today_risk = state["daily_risk"].get(utc_today_str(), {})
    bot.daily_wins = float(today_risk.get("gross_wins", 0.0) or 0.0)
    bot.daily_losses = float(today_risk.get("gross_losses", 0.0) or 0.0)

    for row in state["positions"]:
        pos_key = (row["trader"], row["condition_id"], int(row["outcome_index"]))
        opened_at_ts = _parse_ts(row.get("opened_at_utc", ""))
        bot.positions[pos_key] = {
            "position_id": row["position_id"],
            "condition_id": row["condition_id"],
            "outcome_index": int(row["outcome_index"]),
            "title": row["market"],
            "outcome": row["outcome"],
            "trader": row["trader"],
            "opened_at": opened_at_ts,
            "opened_at_utc": row["opened_at_utc"],
            "total_cost": float(row["total_cost"]),
            "total_shares": float(row["total_shares"]),
            "status": row["status"],
            "pnl": float(row["pnl"]),
            "last_price": (
                float(row["last_price"])
                if row["last_price"] is not None
                else None
            ),
        }

    for fill in state["fills"]:
        record = {
            "timestamp": fill["timestamp_utc"],
            "trader": fill["trader"],
            "market": fill["market"],
            "outcome": fill["outcome"],
            "whale_side": fill["whale_side"],
            "whale_size_usdc": f"{float(fill['whale_size_usdc']):.2f}",
            "our_size_usdc": f"{float(fill['our_size_usdc']):.2f}",
            "price": f"{float(fill['price']):.4f}",
            "copy_shares": f"{float(fill['copy_shares']):.4f}",
            "conviction": f"{float(fill['conviction']):.4f}",
            "status": fill["status"],
            "resolved_pnl": (
                f"{float(fill['resolved_pnl']):+.4f}"
                if fill["resolved_pnl"] is not None
                else ""
            ),
            "condition_id": fill["condition_id"],
            "outcome_index": str(fill["outcome_index"]),
            "event_id": fill["event_id"],
            "position_id": fill["position_id"],
        }
        bot.trade_log.append(record)

    bot.seen_hashes.update(bot.store.load_seen_events())
    return True


def persist_runtime_snapshot(bot: PaperBot):
    for record in bot.trade_log:
        bot.store.upsert_fill(record)
    for pos in bot.positions.values():
        bot.store.update_position(
            pos,
            utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    for trader, stats in bot.trader_stats.items():
        bot.store.set_trader_stats(trader, stats["wins"], stats["losses"])
    bot.store.set_daily_risk(utc_today_str(), bot.daily_wins, bot.daily_losses)
    bot.store.set_value("closed_pnl", bot.closed_pnl)
    bot.store.set_value("wins", bot.wins)
    bot.store.set_value("losses", bot.losses)
    bot.store.set_value("daily_losses_per_trader", bot.daily_losses_per_trader)
    bot.store.set_value("daily_deploy_per_trader", bot.daily_deploy_per_trader)
    bot.store.set_value("milestones_reached", sorted(bot.milestones_reached))
    bot.store.set_value("whale_sizes", bot.whale_sizes)
    bot.store.set_value("budget_day_utc", bot._budget_date.isoformat())


def _validate_runtime_invariants(bot: PaperBot):
    issues = []
    for pos in bot.positions.values():
        related = [r for r in bot.trade_log if r.get("position_id") == pos.get("position_id")]
        fill_cost = sum(float(r.get("our_size_usdc", 0) or 0) for r in related)
        fill_shares = sum(float(r.get("copy_shares", 0) or 0) for r in related)
        if abs(fill_cost - float(pos.get("total_cost", 0) or 0)) > 0.01:
            issues.append(
                f"position {pos.get('position_id')} cost mismatch "
                f"fills={fill_cost:.4f} pos={float(pos.get('total_cost', 0) or 0):.4f}"
            )
        if abs(fill_shares - float(pos.get("total_shares", 0) or 0)) > 0.01:
            issues.append(
                f"position {pos.get('position_id')} shares mismatch "
                f"fills={fill_shares:.4f} pos={float(pos.get('total_shares', 0) or 0):.4f}"
            )

    stat_totals = {}
    for pos in bot.positions.values():
        if pos.get("status") not in ("WIN", "LOSS"):
            continue
        trader = pos.get("trader", "unknown")
        bucket = stat_totals.setdefault(trader, {"wins": 0, "losses": 0})
        if pos["status"] == "WIN":
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    for trader, stats in bot.trader_stats.items():
        expected = stat_totals.get(trader, {"wins": 0, "losses": 0})
        if stats != expected:
            issues.append(
                f"trader_stats mismatch for {trader}: expected {expected}, got {stats}"
            )

    if issues:
        for issue in issues[:5]:
            _log(f"[INVARIANT] {issue}")
        bot.store.set_value("invariant_issues", issues)
    else:
        bot.store.set_value("invariant_issues", [])


def append_csv(record: dict):
    row = {k: record.get(k, "") for k in CSV_FIELDS}
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def update_csv_status(trader: str, cond_id: str, oidx: int, status: str, pnl: float):
    """Rewrite CSV rows for this position with resolved status/PnL."""
    try:
        rows = []
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (
                    row["trader"] == trader and
                    row["condition_id"] == cond_id and
                    row["outcome_index"] == str(oidx)
                ):
                    row["status"]       = status
                    row["resolved_pnl"] = f"{pnl:+.4f}"
                rows.append(row)
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(rows)
    except Exception:
        pass


# ── Filters ───────────────────────────────────────────────────────────────────
def is_crypto(title: str) -> bool:
    low = title.lower()
    return any(kw in low for kw in CRYPTO_KW)


def is_spread(title: str) -> bool:
    """Return True if the market is a spread bet — these are excluded."""
    return bool(re.search(r'\bspread\b', title, re.IGNORECASE))


# ── Trade Processing ──────────────────────────────────────────────────────────
def process_trade(bot: PaperBot, trader_name: str, trade: dict):
    tx      = trade.get("transactionHash", "")
    event_id = make_trade_event_id(trader_name, trade)
    side    = trade.get("side", "").upper()
    usdc    = float(trade.get("usdcSize") or trade.get("size") or 0)
    px      = float(trade.get("price", 0.001))
    cid     = trade.get("conditionId", "")
    oidx    = int(trade.get("outcomeIndex", 0))
    title   = trade.get("title", cid[:30])
    outcome = trade.get("outcome", str(oidx))
    ts      = int(trade.get("timestamp", 0))
    age     = int(time.time()) - ts

    # De-duplicate
    with bot.lock:
        if event_id in bot.seen_hashes:
            return
        bot.seen_hashes.add(event_id)
        # Persist to disk immediately — handles crash/hard-kill restarts
        try:
            with open(SEEN_HASHES_FILE, "w") as f:
                json.dump(list(bot.seen_hashes), f)
        except Exception as e:
            _log(f"Warning: could not persist seen_hashes: {e}")

    # Filters (checked outside lock — pure logic)
    if side != "BUY":                       return  # BUY trades only
    if age  >  MAX_TRADE_AGE:               return  # < 5 minutes old
    if usdc <  MIN_WHALE_SIZE:              return  # min $30 conviction
    if is_crypto(title):                    return  # no crypto markets
    if is_spread(title):                    return  # exclude spread markets — 55% win rate vs 100% O/U
    # Block futures markets — lock capital for weeks/months
    if any(kw in title.lower() for kw in FUTURES_KW):
        bot.status_msg = f"Skipped {title[:50]} — futures market"
        return
    if px >= MAX_ENTRY_PRICE:
        bot.status_msg = f"Skipped {title[:50]} — price {px:.2f} >= cap {MAX_ENTRY_PRICE}"
        return  # poor risk/reward at near-certainty prices

    with bot.lock:
        # Block same-game duplicate — one position per base game at a time.
        # Strips O/U and spread suffixes to find the base game name.
        base_game = re.sub(
            r':\s*(O/U|Spread).*$', '', title, flags=re.IGNORECASE
        ).strip().lower()
        existing_games = {
            re.sub(r':\s*(O/U|Spread).*$', '', p["title"], flags=re.IGNORECASE
                   ).strip().lower()
            for p in bot.positions.values()
            if p.get("status") == "OPEN"
        }
        if base_game in existing_games:
            bot.status_msg = f"Skipped {title[:50]} — already open in this game"
            return

        # Per-trader win-rate gate
        stats = bot.trader_stats.get(trader_name, {"wins": 0, "losses": 0})
        resolved = stats["wins"] + stats["losses"]
        if resolved >= MIN_TRADES_FOR_CUTOFF:
            wr = stats["wins"] / resolved * 100
            if wr < MIN_WIN_RATE:
                bot.status_msg = (
                    f"Skipped {trader_name} — win rate {wr:.1f}% below {MIN_WIN_RATE}%"
                )
                return

        # Per-trader daily loss limit
        today_losses = bot.daily_losses_per_trader.get(trader_name, 0)
        if today_losses >= MAX_DAILY_LOSSES_PER_TRADER:
            msg = f"Skipping {trader_name} - daily loss limit reached ({today_losses} losses today)"
            _log(msg)
            bot.status_msg = msg
            return

        # Update rolling whale-size window and compute conviction
        bot.whale_sizes.append(usdc)
        if len(bot.whale_sizes) > 30:
            bot.whale_sizes = bot.whale_sizes[-30:]
        median_size = statistics.median(bot.whale_sizes)
        conviction  = round(usdc / max(median_size, 0.01), 4)

        bot._refresh_budget()
        net_loss = bot.daily_losses - bot.daily_wins
        if net_loss >= DAILY_LOSS_CAP:
            msg = (
                f"Daily net loss cap reached "
                f"(${bot.daily_losses:.2f} losses - ${bot.daily_wins:.2f} wins = "
                f"${net_loss:.2f} net). No new trades until midnight UTC."
            )
            _log(msg)
            bot.status_msg = msg
            return

        # Per-trader daily dollar cap
        trader_today_deploy = bot.daily_deploy_per_trader.get(trader_name, 0)
        if trader_today_deploy >= MAX_DAILY_DEPLOY_PER_TRADER:
            bot.status_msg = (
                f"Skipped {trader_name} — daily trader cap "
                f"(${trader_today_deploy:.0f} deployed today)"
            )
            return

        # --- Deployment cap ---
        deployed = sum(
            p["total_cost"]
            for p in bot.positions.values()
            if p.get("status") == "OPEN"
        )
        bankroll = STARTING_BANKROLL + bot.closed_pnl + bot.unrealised_pnl
        # unrealised_pnl included so deployment cap reflects economic reality
        if bankroll > 0 and (deployed / bankroll) >= MAX_DEPLOY_PCT:
            bot.status_msg = f"Deployment cap hit ({deployed:.0f}/{bankroll:.0f} = {deployed/bankroll*100:.1f}%) — skipping trade"
            return

        # --- Bankroll-aware sizing ---
        # Bet is capped at 3.5% of total bankroll to scale down in drawdown.
        # Above ~$857 bankroll this resolves to MAX_BET ($30) and is transparent.
        # Below $857 it shrinks automatically to protect against ruin.
        dynamic_max = bankroll * 0.035
        # Per-trader performance multiplier — sizes up on proven signals,
        # cautious on unproven traders.
        t_stats  = bot.trader_stats.get(trader_name, {"wins": 0, "losses": 0})
        t_total  = t_stats["wins"] + t_stats["losses"]
        t_wr     = t_stats["wins"] / t_total * 100 if t_total > 0 else 0.0

        if t_total < 15:
            perf_mult = 0.75   # unproven — cautious until track record established
        elif t_wr >= 70.0:
            perf_mult = 1.25   # proven high performer — size up
        elif t_wr >= 60.0:
            perf_mult = 1.0    # solid performer — standard sizing
        else:
            perf_mult = 0.75   # below standard but not yet dropped — reduce exposure

        copy_usdc = min(BASE_BET * conviction * perf_mult, MAX_BET, dynamic_max)
        copy_shares = copy_usdc / max(px, 0.001)
        bot.daily_deploy_per_trader[trader_name] = (
            bot.daily_deploy_per_trader.get(trader_name, 0) + copy_usdc
        )

        pos_key = (trader_name, cid, oidx)
        position_id = make_position_id(trader_name, cid, oidx)
        if pos_key not in bot.positions:
            bot.positions[pos_key] = {
                "position_id":   position_id,
                "condition_id":  cid,
                "outcome_index": oidx,
                "title":         title,
                "outcome":       outcome,
                "trader":        trader_name,
                "opened_at":     time.time(),
                "opened_at_utc": utc_now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_cost":    0.0,
                "total_shares":  0.0,
                "status":        "OPEN",
                "pnl":           0.0,
                "last_price":    px,
            }
        pos = bot.positions[pos_key]
        pos["total_cost"]   += copy_usdc
        pos["total_shares"] += copy_shares

        now_str = utc_now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "timestamp":       now_str,
            "trader":          trader_name,
            "market":          title,
            "outcome":         outcome,
            "whale_side":      side,
            "whale_size_usdc": f"{usdc:.2f}",
            "our_size_usdc":   f"{copy_usdc:.2f}",
            "price":           f"{px:.4f}",
            "copy_shares":     f"{copy_shares:.4f}",
            "conviction":      f"{conviction:.4f}",
            "status":          "PENDING",
            "resolved_pnl":    "",
            "condition_id":    cid,
            "outcome_index":   str(oidx),
            "event_id":        event_id,
            "position_id":     position_id,
            "transaction_hash": tx,
            "source_timestamp": ts,
        }
        bot.trade_log.append(record)
        bot.status_msg = (
            f"[NEW TRADE] {trader_name} | {title[:35]} | "
            f"{outcome} @ ${px:.3f} | Whale: ${usdc:,.0f} | Copy: ${copy_usdc:.2f}"
        )

    append_csv(record)
    bot.store.upsert_fill(record)
    bot.store.update_position(pos, now_str)
    bot.store.set_value("whale_sizes", bot.whale_sizes)
    bot.store.set_value("daily_deploy_per_trader", bot.daily_deploy_per_trader)
    _validate_runtime_invariants(bot)


# ── Resolution Checker (background thread) ────────────────────────────────────
def get_price_resolved(cid: str, oidx: int):
    """
    Returns (current_price, is_resolved) for a market outcome.

    Uses the CLOB API (https://clob.polymarket.com/markets/{cid}) which returns
    the correct market directly by condition ID.  The Gamma API condition_ids
    filter was unreliable — it ignored the filter and returned unrelated markets.

    Resolution signal:
      - closed == True  AND  tokens[oidx]['winner'] == True
    Price:
      - tokens[oidx]['price']
    """
    data = get(f"{CLOB_API}/markets/{cid}")
    if not data:
        return None, False

    tokens = data.get("tokens")
    if not tokens or oidx >= len(tokens):
        return None, False

    token = tokens[oidx]

    try:
        px = float(token.get("price", 0))
    except (TypeError, ValueError):
        px = None

    closed      = bool(data.get("closed", False))
    is_winner   = bool(token.get("winner", False))
    # A closed market IS resolved regardless of which outcome won.
    # The price (0.0 for losers, 1.0 for winners) determines WIN/LOSS.
    is_resolved = closed  # resolve both winners AND losers when market closes

    return px, is_resolved


def _init_milestones(bot: PaperBot):
    """
    Pre-populate milestones_reached at startup with every threshold the current
    bankroll already exceeds, so they are silently skipped and never prompted.

    Called once in main() after load_positions_from_csv() has restored closed_pnl.
    Without this, every threshold below STARTING_BANKROLL would fire on the first
    resolved trade of every new session.
    """
    bankroll = STARTING_BANKROLL + bot.closed_pnl + bot.unrealised_pnl
    # unrealised_pnl included so deployment cap reflects economic reality
    for threshold, _ in BANKROLL_SCALE_STEPS:
        if bankroll >= threshold:
            bot.milestones_reached.add(threshold)
    if bot.milestones_reached:
        _log(
            f"  Startup: pre-seeded milestones {sorted(bot.milestones_reached)} "
            f"(bankroll ${bankroll:.2f} — these thresholds will not re-prompt)"
        )


def _check_bankroll_scale(bot: PaperBot):
    """After each resolved trade, check if bankroll has crossed a scaling threshold."""
    global BASE_BET, MAX_BET, DAILY_LOSS_CAP
    bankroll = STARTING_BANKROLL + bot.closed_pnl + bot.unrealised_pnl
    # unrealised_pnl included so deployment cap reflects economic reality
    for threshold, cfg in BANKROLL_SCALE_STEPS:
        if bankroll >= threshold and threshold not in bot.milestones_reached:
            bot.milestones_reached.add(threshold)
            bot.store.set_value("milestones_reached", sorted(bot.milestones_reached))
            _log(
                f"SCALE UP AVAILABLE: Bankroll ${bankroll:.2f} has crossed ${threshold} threshold"
            )
            _log(
                f"  Suggested config: BASE_BET={cfg['BASE_BET']}, "
                f"MAX_BET={cfg['MAX_BET']}, DAILY_LOSS_CAP={cfg['DAILY_LOSS_CAP']}"
            )
            if INTERACTIVE_MODE:
                try:
                    print(
                        f"\n[SCALE UP] Bankroll ${bankroll:.2f} crossed ${threshold} threshold.\n"
                        f"  Suggested: BASE_BET={cfg['BASE_BET']}, "
                        f"MAX_BET={cfg['MAX_BET']}, DAILY_LOSS_CAP={cfg['DAILY_LOSS_CAP']}"
                    )
                    answer = input("Apply new scaling? (y/n): ").strip().lower()
                except EOFError:
                    answer = "n"
            else:
                answer = "n"
                _log(
                    f"[SCALE UP] Service mode — auto-declined. "
                    f"To apply, manually set BASE_BET, MAX_BET, DAILY_LOSS_CAP "
                    f"in the constants block and restart the service."
                )
            if answer == "y":
                BASE_BET       = cfg["BASE_BET"]
                MAX_BET        = cfg["MAX_BET"]
                DAILY_LOSS_CAP = cfg["DAILY_LOSS_CAP"]
                _log("Scaling applied.")
                print("Scaling applied.")
            else:
                _log("Scaling declined, keeping current config.")


def resolve_position_snapshot(bot: PaperBot, key, px, resolved, now_ts: float | None = None):
    trader_name, cid, oidx = key
    now_ts = time.time() if now_ts is None else now_ts

    if px is None and not resolved:
        _log(f"  skip {cid[:20]}…  px=None resolved=False")
        return
    if px is None:
        px = 0.0
        _log(f"  [WARN] {cid[:20]}… resolved but px unknown — forcing close at 0.0")

    with bot.lock:
        pos = bot.positions.get(key)
        if not pos or pos["status"] != "OPEN":
            return
        pos["last_price"] = px

        age_days = (now_ts - pos.get("opened_at", now_ts)) / 86400
        if age_days > STALE_POSITION_DAYS and not pos.get("stale_flagged"):
            pos["stale_flagged"] = True
            bot.status_msg = (
                f"[STALE] {pos['title'][:35]} open {age_days:.0f} days — market may be stuck"
            )

        if resolved:
            bot._refresh_budget()
            proceeds = pos["total_shares"] * px
            pnl      = proceeds - pos["total_cost"]
            pos["pnl"]    = pnl
            pos["status"] = "WIN" if pnl >= 0 else "LOSS"
            bot.closed_pnl += pnl
            if pnl >= 0:
                bot.wins += 1
                bot.daily_wins += pnl
            else:
                bot.losses += 1
                bot.daily_losses += abs(pnl)
            trader_name = pos.get("trader", "unknown")
            s = bot.trader_stats.setdefault(trader_name, {"wins": 0, "losses": 0})
            if pnl >= 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
                bot.daily_losses_per_trader[trader_name] = (
                    bot.daily_losses_per_trader.get(trader_name, 0) + 1
                )
            _auto_drop_trader(bot, trader_name, s)
            result_tag = "WIN" if pnl >= 0 else "LOSS"
            bot.status_msg = (
                f"[RESOLVED] {pos['title'][:30]} | "
                f"{pos['outcome']} → {result_tag} ${pnl:+.2f}"
            )
            _log(
                f"  RESOLVED {cid[:20]}… oidx={oidx} "
                f"px={px:.4f} shares={pos['total_shares']:.4f} "
                f"cost={pos['total_cost']:.2f} pnl={pnl:+.4f} → {result_tag}"
            )
            update_csv_status(trader_name, cid, oidx, pos["status"], pnl)
            bot.store.update_fill_resolution(pos["position_id"], pos["status"], pnl)
            bot.store.update_position(
                pos,
                utc_now().strftime("%Y-%m-%d %H:%M:%S"),
                close_reason="RESOLVED",
            )
            bot.store.set_trader_stats(trader_name, s["wins"], s["losses"])
            bot.store.set_daily_risk(utc_today_str(), bot.daily_wins, bot.daily_losses)
            bot.store.set_value("closed_pnl", bot.closed_pnl)
            bot.store.set_value("wins", bot.wins)
            bot.store.set_value("losses", bot.losses)
            bot.store.set_value("daily_losses_per_trader", bot.daily_losses_per_trader)
            _check_bankroll_scale(bot)
            _validate_runtime_invariants(bot)
            for rec in bot.trade_log:
                if rec.get("position_id") == pos["position_id"]:
                    rec["status"] = pos["status"]
                    rec["resolved_pnl"] = f"{pnl:+.4f}"
            return

        age_hours = age_days * 24
        if px < ZERO_PRICE_GUARD and age_hours > 12:
            _log(f"  [WATCH] {cid[:20]} px={px:.4f} age={age_hours:.1f}h approaching force-close")

        force_zero = px < ZERO_PRICE_GUARD and age_hours > ZERO_PRICE_CLOSE_HOURS
        force_age  = age_hours > MAX_OPEN_HOURS
        if force_zero or force_age:
            bot._refresh_budget()
            close_px   = px if px >= ZERO_PRICE_GUARD else 0.0
            proceeds   = pos["total_shares"] * close_px
            pnl        = proceeds - pos["total_cost"]
            result_tag = "WIN" if pnl >= 0 else "LOSS"
            reason     = "ZERO-PRICE" if force_zero else "MAX-AGE"
            pos["pnl"]    = pnl
            pos["status"] = result_tag
            bot.closed_pnl += pnl
            if pnl >= 0:
                bot.wins += 1
                bot.daily_wins += pnl
            else:
                bot.losses += 1
                bot.daily_losses += abs(pnl)
            trader_name = pos.get("trader", "unknown")
            s = bot.trader_stats.setdefault(trader_name, {"wins": 0, "losses": 0})
            if pnl >= 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
                bot.daily_losses_per_trader[trader_name] = (
                    bot.daily_losses_per_trader.get(trader_name, 0) + 1
                )
            _auto_drop_trader(bot, trader_name, s)
            bot.status_msg = (
                f"[{reason}] {pos['title'][:30]} | "
                f"open {age_hours:.0f}h — forced {result_tag} ${pnl:+.2f}"
            )
            _log(
                f"  [{reason}] {cid[:20]}… oidx={oidx} open {age_hours:.0f}h "
                f"px={close_px:.4f} — forced close as {result_tag} pnl={pnl:+.4f}"
            )
            update_csv_status(trader_name, cid, oidx, result_tag, pnl)
            bot.store.update_fill_resolution(pos["position_id"], result_tag, pnl)
            bot.store.update_position(
                pos,
                utc_now().strftime("%Y-%m-%d %H:%M:%S"),
                close_reason=reason,
            )
            bot.store.set_trader_stats(trader_name, s["wins"], s["losses"])
            bot.store.set_daily_risk(utc_today_str(), bot.daily_wins, bot.daily_losses)
            bot.store.set_value("closed_pnl", bot.closed_pnl)
            bot.store.set_value("wins", bot.wins)
            bot.store.set_value("losses", bot.losses)
            bot.store.set_value("daily_losses_per_trader", bot.daily_losses_per_trader)
            _check_bankroll_scale(bot)
            _validate_runtime_invariants(bot)
            for rec in bot.trade_log:
                if rec.get("position_id") == pos["position_id"]:
                    rec["status"] = result_tag
                    rec["resolved_pnl"] = f"{pnl:+.4f}"
            return

        pos["pnl"] = pos["total_shares"] * px - pos["total_cost"]
        bot.store.update_position(
            pos,
            utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        _validate_runtime_invariants(bot)


def _auto_drop_trader(bot: PaperBot, trader_name: str, s: dict):
    """
    Two-tier exit system optimised for capital preservation + growth.

    Tier 1 — Temporary drop (15 trades, <55% WR):
        Remove from active watchlist until next 6-hour leaderboard refresh.
        55% is the mathematical break-even floor at current R/R ratio.
        15 trades gives statistical confidence without being trigger-happy.

    Tier 2 — Permanent block (25 trades, <50% WR):
        Add to TRADER_BLOCKLIST. No comeback via leaderboard refresh.
        At 25 trades below 50%, this is a bad signal not a cold spell.
    """
    _total = s["wins"] + s["losses"]
    _wr = s["wins"] / _total * 100 if _total > 0 else 0.0

    # Tier 2: permanent block — 25+ trades below 50%
    if _total >= 25 and _wr < 50.0:
        if trader_name.lower() not in TRADER_BLOCKLIST:
            TRADER_BLOCKLIST.add(trader_name.lower())
            _log(
                f"  [PERM-BLOCK] {trader_name} added to permanent blocklist "
                f"(observed WR {_wr:.1f}% over {_total} trades < 50% floor). "
                f"Will not be re-qualified by leaderboard refresh."
            )
            bot.status_msg = (
                f"[PERM-BLOCK] {trader_name} permanently blocked — "
                f"{_wr:.1f}% WR over {_total} trades"
            )

    # Tier 1: temporary drop — 15+ trades below 55%
    if _total >= 15 and _wr < 55.0:
        if trader_name in bot.trader_addrs:
            del bot.trader_addrs[trader_name]
            try:
                from dynamic_watchlist import AddressCache, CACHE_FILE
                _cache = AddressCache(CACHE_FILE)
                if trader_name in _cache._data:
                    _cache._data[trader_name]["active"] = False
                    _cache._save()
            except Exception:
                pass
            if _wr >= 50.0:
                _log(
                    f"  [AUTO-DROP] {trader_name} removed from active watchlist "
                    f"(observed WR {_wr:.1f}% over {_total} trades < 55% break-even). "
                    f"Can re-qualify on next leaderboard refresh."
                )
                bot.status_msg = (
                    f"[AUTO-DROP] {trader_name} dropped — "
                    f"{_wr:.1f}% WR over {_total} trades"
                )


def resolution_loop(bot: PaperBot):
    """Periodically price open positions and mark resolved ones WIN/LOSS."""
    while True:
        time.sleep(RESOLVE_INTERVAL)
        with bot.lock:
            open_keys = [k for k, p in bot.positions.items() if p["status"] == "OPEN"]

        _log(f"resolution_loop: checking {len(open_keys)} open position(s)")

        for key in open_keys:
            _, cid, oidx = key
            px, resolved = get_price_resolved(cid, oidx)
            resolve_position_snapshot(bot, key, px, resolved)
            time.sleep(0.15)


# ── Dashboard (Rich) ──────────────────────────────────────────────────────────
def make_header(bot: PaperBot) -> Panel:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pnl_col = "green" if bot.total_pnl >= 0 else "red"
    ur_col  = "green" if bot.unrealised_pnl >= 0 else "red"
    api_warn = (
        f"  [bold red][WARN] API failures: {bot.api_fail_count}[/bold red]"
        if bot.api_fail_count >= MAX_API_FAILURES else ""
    )

    # Per-trader stat summary
    trader_parts = []
    for name, s in bot.trader_stats.items():
        total = s["wins"] + s["losses"]
        wr = s["wins"] / total * 100 if total else 0.0
        col = "green" if wr >= MIN_WIN_RATE else "red"
        trader_parts.append(f"[{col}]{name}: {wr:.0f}% ({total})[/]")
    trader_line = "  ".join(trader_parts) if trader_parts else "[dim]no resolved trades yet[/dim]"

    grid = Table.grid(padding=(0, 3))
    grid.add_column(width=30)
    grid.add_column(width=30)
    grid.add_column(width=30)
    grid.add_row(
        f"[bold {pnl_col}]Simul. PnL   ${bot.total_pnl:+,.2f}[/]",
        f"[bold]Win Rate  {bot.win_rate:.1f}%  ({bot.wins}W / {bot.losses}L)[/]",
        f"[bold]Net loss today ${bot.daily_net_loss:+.2f}  /  cap ${DAILY_LOSS_CAP:.0f}[/]",
    )
    grid.add_row(
        f"[{ur_col}]Unrealised   ${bot.unrealised_pnl:+,.2f}[/]",
        f"Open positions: [bold]{len(bot.open_positions)}[/bold]",
        f"[dim]Last poll: {bot.last_poll}[/dim]",
    )
    grid.add_row(
        f"Traders: {trader_line}",
        api_warn,
        "",
    )
    return Panel(
        grid,
        title=(
            "[bold white on red]"
            "  *** PAPER MODE — SIMULATION ONLY — NO REAL MONEY ***  "
            "[/bold white on red]"
        ),
        subtitle=f"[dim]{now}[/dim]",
        border_style="red",
    )


def make_positions_table(bot: PaperBot) -> Table:
    t = Table(
        title="[bold yellow]Active Open Positions[/bold yellow]",
        box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan",
        padding=(0, 1),
    )
    t.add_column("Market",   max_width=40)
    t.add_column("Outcome",  width=9,  justify="center")
    t.add_column("Cost",     width=8,  justify="right")
    t.add_column("Price",    width=7,  justify="right")
    t.add_column("Unreal",   width=10, justify="right")

    positions = sorted(bot.open_positions, key=lambda p: p["total_cost"], reverse=True)
    if not positions:
        t.add_row("[dim]No open positions yet — waiting for qualifying trades...[/dim]",
                  "", "", "", "")
        return t

    for pos in positions[:14]:
        col = "green" if pos["pnl"] >= 0 else "red"
        t.add_row(
            pos["title"][:40],
            pos["outcome"][:9],
            f"${pos['total_cost']:.2f}",
            f"${pos['last_price']:.3f}",
            f"[{col}]${pos['pnl']:+.2f}[/]",
        )
    return t


def make_trades_table(bot: PaperBot) -> Table:
    t = Table(
        title="[bold yellow]Trade Log (last 15)[/bold yellow]",
        box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan",
        padding=(0, 1),
    )
    t.add_column("Time",     width=10)
    t.add_column("Trader",   width=18)
    t.add_column("Market",   max_width=34)
    t.add_column("Outcome",  width=9,  justify="center")
    t.add_column("Whale $",  width=10, justify="right")
    t.add_column("Copy $",   width=8,  justify="right")
    t.add_column("Status",   width=9,  justify="center")

    if not bot.trade_log:
        t.add_row("[dim]No trades yet...[/dim]", "", "", "", "", "", "")
        return t

    for r in reversed(bot.trade_log[-15:]):
        s   = r.get("status", "PENDING")
        col = "green" if s == "WIN" else ("red" if s == "LOSS" else "yellow")
        t.add_row(
            r["timestamp"][11:19],
            r["trader"],
            r["market"][:34],
            r["outcome"][:9],
            f"${float(r['whale_size_usdc']):,.0f}",
            f"${float(r['our_size_usdc']):.2f}",
            f"[{col}]{s}[/]",
        )
    return t


def make_dashboard(bot: PaperBot):
    status_line = Panel(
        f"[dim]{bot.status_msg}[/dim]   |   Log: [cyan]{CSV_FILE.resolve()}[/cyan]",
        title="[dim]Status[/dim]",
        border_style="dim",
        padding=(0, 1),
    )
    return Group(
        make_header(bot),
        make_positions_table(bot),
        make_trades_table(bot),
        status_line,
    )


# ── Polling ───────────────────────────────────────────────────────────────────
def poll_once(bot: PaperBot):
    any_success = False
    with bot.lock:
        addrs = dict(bot.trader_addrs)
    for name, addr in addrs.items():
        data = get(f"{DATA_API}/trades", {
            "user": addr, "limit": 50, "offset": 0, "takerOnly": "false"
        })
        if not data or not isinstance(data, list):
            continue
        any_success = True
        cutoff = int(time.time()) - MAX_TRADE_AGE   # look back exactly MAX_TRADE_AGE (5 min)
        for trade in data:
            if trade.get("type", "TRADE") != "TRADE":
                continue  # skip REDEEM, YIELD and other non-trade events
            if int(trade.get("timestamp", 0)) >= cutoff:
                process_trade(bot, name, trade)
        time.sleep(0.3)

    with bot.lock:
        if any_success:
            bot.api_fail_count = 0
        else:
            bot.api_fail_count += 1
            if bot.api_fail_count >= MAX_API_FAILURES:
                bot.status_msg = (
                    f"[WARN] API unreachable — {bot.api_fail_count} consecutive failures"
                )
        bot.last_poll = utc_now().strftime("%H:%M:%S UTC")
        _sync_health(bot)


# ── Plain-text fallback dashboard ─────────────────────────────────────────────
def print_plain_dashboard(bot: PaperBot):
    os.system("cls" if os.name == "nt" else "clear")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print("=" * 66)
    print("  *** PAPER MODE -- SIMULATION ONLY -- NO REAL MONEY ***")
    print("=" * 66)
    print(f"  {now}")
    print(f"  Simulated PnL  : ${bot.total_pnl:+,.2f}  "
          f"(unrealised: ${bot.unrealised_pnl:+,.2f})")
    print(f"  Win Rate       : {bot.win_rate:.1f}%  "
          f"({bot.wins}W / {bot.losses}L)")
    print(f"  Daily Net Loss : ${bot.daily_losses:.2f} losses - ${bot.daily_wins:.2f} wins = "
          f"${bot.daily_net_loss:+.2f}  (cap ${DAILY_LOSS_CAP:.0f})")
    print(f"  Open Positions : {len(bot.open_positions)}")
    print()

    if bot.open_positions:
        print("  Open Positions:")
        for pos in sorted(bot.open_positions, key=lambda p: p["total_cost"], reverse=True)[:8]:
            sign = "+" if pos["pnl"] >= 0 else ""
            print(f"    {pos['title'][:40]:<40} | {pos['outcome']:<8} "
                  f"| cost ${pos['total_cost']:.2f} | pnl {sign}${pos['pnl']:.2f}")
        print()

    if bot.trade_log:
        print("  Recent Trades:")
        for r in reversed(bot.trade_log[-8:]):
            print(f"    [{r['timestamp'][11:19]}] {r['trader']:<16} | "
                  f"{r['market'][:34]:<34} | "
                  f"whale ${float(r['whale_size_usdc']):>8,.0f} | "
                  f"copy ${float(r['our_size_usdc']):.2f} | {r['status']}")
        print()

    if bot.trader_stats:
        print("  Trader Win Rates:")
        for name, s in bot.trader_stats.items():
            total = s["wins"] + s["losses"]
            wr = s["wins"] / total * 100 if total else 0.0
            flag = " [BELOW THRESHOLD]" if total >= MIN_TRADES_FOR_CUTOFF and wr < MIN_WIN_RATE else ""
            print(f"    {name:<20} {wr:.1f}%  ({s['wins']}W / {s['losses']}L){flag}")
        print()

    if bot.api_fail_count >= MAX_API_FAILURES:
        print(f"  [WARN] API unreachable — {bot.api_fail_count} consecutive failures")
        print()

    print(f"  Status : {bot.status_msg}")
    print(f"  Log    : {CSV_FILE.resolve()}")
    print("=" * 66)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 66)
    print("  *** POLYMARKET PAPER TRADING BOT ***")
    print("  *** PAPER MODE — NO REAL MONEY — NO WALLET CONNECTED ***")
    print("=" * 66)
    if USE_DYNAMIC_WATCHLIST:
        print(f"  Watchlist  : dynamic top {WATCHLIST_TOP_N} (min WR {WATCHLIST_MIN_WR:.0f}%, 6h refresh)")
    else:
        print(f"  Copying    : {', '.join(TRADERS_TO_COPY)}  [static list]")
    print(f"  Sizing     : base ${BASE_BET} / max ${MAX_BET}  |  Daily loss cap: ${DAILY_LOSS_CAP}  |  Min whale: ${MIN_WHALE_SIZE}")
    print(f"  Poll cycle : every {POLL_INTERVAL}s  |  Max trade age: {MAX_TRADE_AGE}s")
    print(f"  Trade log  : {CSV_FILE.resolve()}")
    print(f"  Build      : {get_build_version()}")
    print()

    bot = PaperBot()
    migrate_csv()                  # fix stale header before reading or writing
    init_csv()
    loaded_from_store = load_positions_from_store(bot)
    if not loaded_from_store:
        load_positions_from_csv(bot)
    _init_milestones(bot)   # must come after CSV load so closed_pnl is accurate
    if not loaded_from_store:
        persist_runtime_snapshot(bot)
    _validate_runtime_invariants(bot)
    if bot.positions:
        print(f"  Reloaded {len(bot.positions)} open position(s) from previous run.")

    # ── Resolve / load trader addresses ──────────────────────────────────────
    if USE_DYNAMIC_WATCHLIST:
        # Dynamic mode: score leaderboard by PNL, filter by win rate, cache addresses.
        # WatchlistManager.start() is blocking for the initial load, then spawns
        # its own daemon thread for 6-hour refreshes — do NOT start address_refresh_loop.
        print(f"  Starting dynamic watchlist (top {WATCHLIST_TOP_N}, min WR {WATCHLIST_MIN_WR:.0f}%)...")
        print(f"  (Estimating win rates via Gamma API — may take ~30–90 seconds)")
        wl_manager = WatchlistManager(
            top_n=WATCHLIST_TOP_N,
            min_wr=WATCHLIST_MIN_WR,
            refresh_hours=WATCHLIST_REFRESH_H,
            log_fn=print,
        )
        wl_manager.start(bot)   # populates bot.trader_addrs; spawns refresh thread
        if not bot.trader_addrs:
            sys.exit("[FATAL] Dynamic watchlist found no traders passing the WR filter.")
    else:
        # Legacy mode: look up hardcoded TRADERS_TO_COPY names on the leaderboard.
        print("  Resolving trader addresses (static list)...")
        for attempt in range(3):
            bot.trader_addrs = resolve_addresses()
            if bot.trader_addrs:
                break
            print(f"  Retry {attempt + 1}/3 in 5s...")
            time.sleep(5)
        if not bot.trader_addrs:
            sys.exit("[FATAL] Could not connect to Polymarket API.")
        missing = [n for n in TRADERS_TO_COPY if n not in bot.trader_addrs]
        if missing:
            print(f"\n  [WARN] Not on leaderboard this month: {missing}")
            print("         May have dropped out of top 50 — will copy whoever is found.")
        # Static mode uses the original address refresh loop
        threading.Thread(target=address_refresh_loop, args=(bot,), daemon=True).start()

    print()
    for name, addr in bot.trader_addrs.items():
        print(f"  Watching : {name}  ->  {addr}")

    if not bot.trader_addrs:
        sys.exit("[FATAL] No target traders found.")

    # Seed seen hashes to avoid replaying historical trades on startup
    print("\n  Seeding trade history (prevents false triggers on startup)...")
    seed_seen_hashes(bot)
    print(f"  {len(bot.seen_hashes)} recent transactions indexed.")

    # Background threads (resolution loop always runs; address refresh only in legacy mode)
    threading.Thread(target=resolution_loop, args=(bot,), daemon=True).start()

    _log(f"Price cap: ACTIVE — entries >= ${MAX_ENTRY_PRICE} will be skipped")
    _log(f"Interactive mode: {'ON' if INTERACTIVE_MODE else 'OFF (service mode)'}")
    _log(f"Build version: {get_build_version()}")
    bot.status_msg = "Live — scanning for qualifying BUY trades..."

    print(f"\n  [PAPER MODE] Monitoring started. Press Ctrl+C to stop.\n")

    if HAS_RICH:
        _rich_loop(bot)
    else:
        print("  Tip: pip install rich  for the live dashboard\n")
        _plain_loop(bot)


LOG_FILE = Path("bot.log")


def _log(msg: str):
    """Append a timestamped diagnostic line to bot.log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} - {msg}\n")
    except Exception:
        pass


def _write_heartbeat():
    _log("heartbeat")


def _sync_health(bot: PaperBot):
    bot.store.set_value("health", {
        "status_msg": bot.status_msg,
        "last_poll": bot.last_poll,
        "api_fail_count": bot.api_fail_count,
        "last_addr_refresh": bot.last_addr_refresh,
        "last_heartbeat_utc": utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        "build_version": get_build_version(),
    })


def _rich_loop(bot: PaperBot):
    console = Console()
    with Live(
        make_dashboard(bot),
        console=console,
        refresh_per_second=1,
        screen=True,
        vertical_overflow="visible",
    ) as live:
        while True:
            poll_once(bot)
            _write_heartbeat()
            # Refresh every second while waiting for next poll
            next_poll = time.time() + POLL_INTERVAL
            while time.time() < next_poll:
                time.sleep(1)
                live.update(make_dashboard(bot))


def _plain_loop(bot: PaperBot):
    while True:
        poll_once(bot)
        _write_heartbeat()
        print_plain_dashboard(bot)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
