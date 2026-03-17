"""
Polymarket Paper Trading Bot
============================
[PAPER MODE] — Simulated trades only. No real money. No wallet connected.

Copies: majorexploiter, beachboy4
Ratio:  10:1  |  Daily budget: $50  |  Min whale: $200
Poll:   every 30 seconds
"""

import csv, json, os, sys, time, threading
from datetime import datetime, date, timezone
from pathlib import Path

import requests

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
COPY_RATIO             = 0.10    # 1/10th of whale trade size
DAILY_BUDGET           = 50.0   # simulated USD per calendar day
MIN_WHALE_SIZE         = 200.0  # minimum whale USDC size to copy (raised for conviction)
MAX_TRADE_AGE          = 300    # 5 minutes in seconds
POLL_INTERVAL          = 30     # seconds between wallet polls
RESOLVE_INTERVAL       = 60     # seconds between resolution checks
ADDRESS_REFRESH_HOURS  = 6      # how often to re-resolve trader addresses
MIN_TRADES_FOR_CUTOFF  = 10     # min resolved trades before applying win-rate gate
MIN_WIN_RATE           = 40.0   # % — stop copying a trader below this threshold
STALE_POSITION_DAYS    = 30     # flag positions open longer than this
MAX_API_FAILURES       = 5      # consecutive poll failures before status warning

TRADERS_TO_COPY  = ["majorexploiter", "beachboy4"]
CSV_FILE         = Path("paper_trades.csv")

CRYPTO_KW = {
    "bitcoin","btc","ethereum","eth","crypto","solana","sol","xrp",
    "ripple","dogecoin","doge","bnb","binance","nft","blockchain",
    "defi","polygon","matic","avalanche","avax","chainlink","cardano",
    "ada","litecoin","ltc","usdc","usdt","stablecoin","web3","token",
}

CSV_FIELDS = [
    "timestamp","trader","market","outcome","whale_side",
    "whale_size_usdc","our_size_usdc","price","copy_shares",
    "status","resolved_pnl","condition_id","outcome_index",
]

# ── Bot State ─────────────────────────────────────────────────────────────────
class PaperBot:
    def __init__(self):
        self.lock               = threading.Lock()
        self.trader_addrs       = {}        # name → proxyWallet address
        self.seen_hashes        = set()     # processed tx hashes
        self.positions          = {}        # (cond_id, oidx) → position dict
        self.trade_log          = []        # list of trade record dicts
        self._daily_used        = 0.0
        self._budget_date       = date.today()
        self.closed_pnl         = 0.0      # realised PnL only
        self.wins               = 0
        self.losses             = 0
        self.status_msg         = "Starting up..."
        self.last_poll          = "Never"
        self.trader_stats       = {}        # name → {"wins": int, "losses": int}
        self.api_fail_count     = 0         # consecutive poll cycles with all-None responses
        self.last_addr_refresh  = time.time()

    def _refresh_budget(self):
        today = date.today()
        if today != self._budget_date:
            self._daily_used  = 0.0
            self._budget_date = today

    @property
    def daily_remaining(self):
        self._refresh_budget()
        return max(0.0, DAILY_BUDGET - self._daily_used)

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
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i < retries - 1:
                time.sleep(2 ** i)   # 1s, 2s backoff
    return None


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
    cutoff = int(time.time()) - 3600
    for name, addr in bot.trader_addrs.items():
        data = get(f"{DATA_API}/trades", {
            "user": addr, "limit": 100, "offset": 0, "takerOnly": "false"
        })
        if data and isinstance(data, list):
            for t in data:
                if int(t.get("timestamp", 0)) >= cutoff:
                    h = t.get("transactionHash", "")
                    if h:
                        bot.seen_hashes.add(h)
        time.sleep(0.3)


# ── CSV ───────────────────────────────────────────────────────────────────────
def init_csv():
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def load_positions_from_csv(bot: PaperBot):
    """Reload open positions from CSV so the resolution loop can close them after a restart."""
    if not CSV_FILE.exists():
        return
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") not in ("PENDING", "OPEN"):
                    continue
                cid  = row.get("condition_id", "")
                oidx = int(row.get("outcome_index", 0))
                if not cid:
                    continue
                pos_key = (cid, oidx)
                cost    = float(row.get("our_size_usdc", 0) or 0)
                price   = float(row.get("price", 0.001) or 0.001)
                shares  = float(row.get("copy_shares", 0) or 0)
                if pos_key not in bot.positions:
                    bot.positions[pos_key] = {
                        "condition_id":  cid,
                        "outcome_index": oidx,
                        "title":         row.get("market", cid[:30]),
                        "outcome":       row.get("outcome", str(oidx)),
                        "trader":        row.get("trader", "unknown"),
                        "opened_at":     time.time(),   # approx; exact age not critical
                        "total_cost":    0.0,
                        "total_shares":  0.0,
                        "status":        "OPEN",
                        "pnl":           0.0,
                        "last_price":    price,
                    }
                pos = bot.positions[pos_key]
                pos["total_cost"]   += cost
                pos["total_shares"] += shares
                bot.trade_log.append(dict(row))
    except Exception:
        pass


def append_csv(record: dict):
    row = {k: record.get(k, "") for k in CSV_FIELDS}
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def update_csv_status(cond_id: str, oidx: int, status: str, pnl: float):
    """Rewrite CSV rows for this position with resolved status/PnL."""
    try:
        rows = []
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["condition_id"] == cond_id and row["outcome_index"] == str(oidx):
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


# ── Trade Processing ──────────────────────────────────────────────────────────
def process_trade(bot: PaperBot, trader_name: str, trade: dict):
    tx      = trade.get("transactionHash", "")
    side    = trade.get("side", "").upper()
    usdc    = float(trade.get("size", 0))
    px      = float(trade.get("price", 0.001))
    cid     = trade.get("conditionId", "")
    oidx    = int(trade.get("outcomeIndex", 0))
    title   = trade.get("title", cid[:30])
    outcome = trade.get("outcome", str(oidx))
    ts      = int(trade.get("timestamp", 0))
    age     = int(time.time()) - ts

    # De-duplicate
    with bot.lock:
        if tx and tx in bot.seen_hashes:
            return
        bot.seen_hashes.add(tx)

    # Filters (checked outside lock — pure logic)
    if side != "BUY":                       return  # BUY trades only
    if age  >  MAX_TRADE_AGE:               return  # < 5 minutes old
    if usdc <  MIN_WHALE_SIZE:              return  # min $30 conviction
    if is_crypto(title):                    return  # no crypto markets

    with bot.lock:
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

        bot._refresh_budget()
        remaining = bot.daily_remaining
        if remaining < 0.01:
            bot.status_msg = f"Budget exhausted — skipped: {title[:30]}"
            return

        copy_usdc   = min(usdc * COPY_RATIO, remaining)
        copy_shares = copy_usdc / max(px, 0.001)
        bot._daily_used += copy_usdc

        pos_key = (cid, oidx)
        if pos_key not in bot.positions:
            bot.positions[pos_key] = {
                "condition_id":  cid,
                "outcome_index": oidx,
                "title":         title,
                "outcome":       outcome,
                "trader":        trader_name,
                "opened_at":     time.time(),
                "total_cost":    0.0,
                "total_shares":  0.0,
                "status":        "OPEN",
                "pnl":           0.0,
                "last_price":    px,
            }
        pos = bot.positions[pos_key]
        pos["total_cost"]   += copy_usdc
        pos["total_shares"] += copy_shares

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
            "status":          "PENDING",
            "resolved_pnl":    "",
            "condition_id":    cid,
            "outcome_index":   str(oidx),
        }
        bot.trade_log.append(record)
        bot.status_msg = (
            f"[NEW TRADE] {trader_name} | {title[:35]} | "
            f"{outcome} @ ${px:.3f} | Whale: ${usdc:,.0f} | Copy: ${copy_usdc:.2f}"
        )

    append_csv(record)


# ── Resolution Checker (background thread) ────────────────────────────────────
def get_price_resolved(cid: str, oidx: int):
    """
    Returns (current_price, is_resolved) for a market outcome.

    KEY FINDINGS from live API testing against gamma-api.polymarket.com:

    1. The 'resolved' field is always None — never True/False.
       bool(None) == False, so the original check 'if resolved:' NEVER fired.
       The correct resolution signal is 'closed: True'.

    2. 'condition_ids' (snake_case) IS the correct query parameter — confirmed
       by comparing the returned conditionId against the queried one.

    3. A closed market is truly SETTLED when at least one outcomePrices entry
       is >= 0.99 (i.e. a clear winner has emerged). Markets that are closed
       but still pending resolution show mid-range or all-zero prices.

    4. We find the matching market in the response list by conditionId field
       rather than blindly taking index 0, guarding against any ordering
       changes in the API response.
    """
    data = get(f"{GAMMA_API}/markets", {"condition_ids": cid})
    if not data:
        return None, False

    if isinstance(data, list):
        # Find the market that matches our condition ID exactly
        m = next(
            (item for item in data if item.get("conditionId") == cid),
            data[0]  # fall back to first item if conditionId field name differs
        )
    else:
        m = data

    closed = bool(m.get("closed", False))
    prices_raw = m.get("outcomePrices", "[]")

    # Parse stringified JSON arrays (API returns them as JSON strings)
    if isinstance(prices_raw, str):
        try:
            prices = json.loads(prices_raw)
        except Exception:
            prices = []
    else:
        prices = prices_raw or []

    # Get this outcome's current price
    try:
        px = float(prices[oidx])
    except (IndexError, TypeError, ValueError):
        px = None

    # A market is truly SETTLED when:
    #   - closed == True  (trading has stopped), AND
    #   - at least one outcome price >= 0.99  (a clear winner is priced in)
    # Markets that closed but haven't resolved yet (pending arbitration, etc.)
    # show mid-range or all-zero prices, which we correctly leave as OPEN.
    is_resolved = False
    if closed and prices:
        try:
            float_prices = [float(p) for p in prices]
            if max(float_prices) >= 0.99:
                is_resolved = True
        except (ValueError, TypeError):
            pass

    return px, is_resolved


def resolution_loop(bot: PaperBot):
    """Periodically price open positions and mark resolved ones WIN/LOSS."""
    while True:
        time.sleep(RESOLVE_INTERVAL)
        with bot.lock:
            open_keys = [k for k, p in bot.positions.items() if p["status"] == "OPEN"]

        _log(f"resolution_loop: checking {len(open_keys)} open position(s)")

        for key in open_keys:
            cid, oidx = key
            px, resolved = get_price_resolved(cid, oidx)

            # Only skip if we have NO price AND the market is genuinely not resolved.
            # A resolved market must never be left PENDING due to a price parse failure.
            if px is None and not resolved:
                _log(f"  skip {cid[:20]}…  px=None resolved=False")
                continue
            if px is None:
                # Resolved but price still indeterminate after all fallbacks.
                # Close at 0.0 (worst-case loss) so the position doesn't stay PENDING.
                px = 0.0
                _log(f"  [WARN] {cid[:20]}… resolved but px unknown — forcing close at 0.0")

            with bot.lock:
                pos = bot.positions.get(key)
                if not pos or pos["status"] != "OPEN":
                    continue
                pos["last_price"] = px

                # Flag stale positions
                age_days = (time.time() - pos.get("opened_at", time.time())) / 86400
                if age_days > STALE_POSITION_DAYS and not pos.get("stale_flagged"):
                    pos["stale_flagged"] = True
                    bot.status_msg = (
                        f"[STALE] {pos['title'][:35]} open {age_days:.0f} days — market may be stuck"
                    )

                if resolved:
                    proceeds = pos["total_shares"] * px
                    pnl      = proceeds - pos["total_cost"]
                    pos["pnl"]    = pnl
                    pos["status"] = "WIN" if pnl >= 0 else "LOSS"
                    bot.closed_pnl += pnl
                    if pnl >= 0:
                        bot.wins += 1
                    else:
                        bot.losses += 1
                    # Update per-trader stats
                    trader_name = pos.get("trader", "unknown")
                    s = bot.trader_stats.setdefault(trader_name, {"wins": 0, "losses": 0})
                    if pnl >= 0:
                        s["wins"] += 1
                    else:
                        s["losses"] += 1
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
                    update_csv_status(cid, oidx, pos["status"], pnl)
                    # Sync in-memory trade log so the dashboard reflects WIN/LOSS
                    for rec in bot.trade_log:
                        if rec.get("condition_id") == cid and rec.get("outcome_index") == str(oidx):
                            rec["status"]       = pos["status"]
                            rec["resolved_pnl"] = f"{pnl:+.4f}"
                else:
                    pos["pnl"] = pos["total_shares"] * px - pos["total_cost"]
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
        f"[bold]Budget  used ${bot._daily_used:.2f}  /  left ${bot.daily_remaining:.2f}[/]",
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
        cutoff = int(time.time()) - 600   # look back 10 min each poll
        for trade in data:
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
        bot.last_poll = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


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
    print(f"  Daily Budget   : ${bot._daily_used:.2f} used  /  "
          f"${bot.daily_remaining:.2f} remaining")
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
    print(f"  Copying    : {', '.join(TRADERS_TO_COPY)}")
    print(f"  Ratio      : 10:1  |  Budget: ${DAILY_BUDGET}/day  |  Min whale: ${MIN_WHALE_SIZE}")
    print(f"  Poll cycle : every {POLL_INTERVAL}s")
    print(f"  Trade log  : {CSV_FILE.resolve()}")
    print()

    bot = PaperBot()
    init_csv()
    load_positions_from_csv(bot)
    if bot.positions:
        print(f"  Reloaded {len(bot.positions)} open position(s) from previous run.")

    # Resolve trader addresses from leaderboard
    print("  Resolving trader addresses...")
    for attempt in range(3):
        bot.trader_addrs = resolve_addresses()
        if bot.trader_addrs:
            break
        print(f"  Retry {attempt + 1}/3 in 5s...")
        time.sleep(5)

    if not bot.trader_addrs:
        sys.exit("[FATAL] Could not connect to Polymarket API.")

    for name, addr in bot.trader_addrs.items():
        print(f"  Found  : {name} -> {addr}")

    missing = [n for n in TRADERS_TO_COPY if n not in bot.trader_addrs]
    if missing:
        print(f"\n  [WARN] Not on leaderboard this month: {missing}")
        print("         May have dropped out of top 50 — will copy whoever is found.")

    if not bot.trader_addrs:
        sys.exit("[FATAL] No target traders found on leaderboard.")

    # Seed seen hashes to avoid replaying historical trades on startup
    print("\n  Seeding trade history (prevents false triggers on startup)...")
    seed_seen_hashes(bot)
    print(f"  {len(bot.seen_hashes)} recent transactions indexed.")

    # Background threads
    threading.Thread(target=resolution_loop,   args=(bot,), daemon=True).start()
    threading.Thread(target=address_refresh_loop, args=(bot,), daemon=True).start()

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
