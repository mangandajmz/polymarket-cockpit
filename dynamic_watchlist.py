"""
dynamic_watchlist.py
====================
Dynamic trader watchlist for the Polymarket paper trading bot.

Selection criteria:
  - Ranked by monthly PNL (leaderboard)
  - Minimum win rate filter (default 60%)
  - Top N qualifying traders kept (default 5)

Permanent cache:
  watchlist_cache.json is append-only. Trader addresses are never deleted,
  so the resolution loop can still close positions from traders who have
  since dropped off the leaderboard.

Refresh: every REFRESH_HOURS (6) hours via background daemon thread.
The manager pushes updates directly to bot.trader_addrs on each refresh.

Win rate estimation:
  Samples up to WR_SAMPLE (10) of the trader's most recent positions,
  prices them via the Gamma API, and computes what fraction are currently
  profitable. This is the same approach used in the backtest.
"""

import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Module-level API config ───────────────────────────────────────────────────
_DATA_API   = "https://data-api.polymarket.com/v1"
_GAMMA_API  = "https://gamma-api.polymarket.com"
_WR_SAMPLE  = 10    # positions to price per candidate (speed vs accuracy trade-off)
_REQ_PAUSE  = 0.25  # seconds between Gamma API calls during WR estimation

CACHE_FILE  = Path("watchlist_cache.json")


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _req(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
    return None


# ── Permanent address cache ───────────────────────────────────────────────────

class AddressCache:
    """
    Append-only store of {trader_name: proxyWallet}.

    Entries are written the first time a trader qualifies for the watchlist
    and are never removed. This ensures that:
      - A trader who drops off the leaderboard mid-month can still have their
        open positions resolved by the bot's resolution loop.
      - The bot never loses track of an address it has already acted on.
    """

    def __init__(self, path: Path = CACHE_FILE):
        self.path   = path
        self._data: dict = {}
        self._meta: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                # Support both old format (flat dict) and new format (with _meta key)
                self._data = {k: v for k, v in raw.items() if not k.startswith("_")}
                self._meta = {k: v for k, v in raw.items() if k.startswith("_")}
            except Exception:
                self._data = {}
                self._meta = {}
        else:
            self._meta = {}

    def _save(self):
        combined = {**self._data, **self._meta}
        self.path.write_text(
            json.dumps(combined, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def set_last_successful_refresh(self, ts: str):
        self._meta["_last_successful_refresh"] = ts
        self._save()

    def get_last_successful_refresh(self) -> str:
        return self._meta.get("_last_successful_refresh", "never")

    def update(self, entries: dict):
        """Merge new {name: addr} pairs. Existing entries are never overwritten."""
        changed = False
        for name, addr in entries.items():
            if name not in self._data and addr:
                self._data[name] = addr
                changed = True
        if changed:
            self._save()

    def get_all(self) -> dict:
        return dict(self._data)

    def __len__(self):
        return len(self._data)


# ── Win rate estimation ───────────────────────────────────────────────────────

def _estimate_win_rate(address: str, sample: int = _WR_SAMPLE) -> float:
    """
    Estimate a trader's win rate from their most recent positions.

    Method:
      1. Fetch up to 500 recent trades from DATA_API.
      2. Build (conditionId, outcomeIndex) positions: net USDC in/out and shares.
      3. Sample up to `sample` positions (most recently bought first).
      4. For each sampled position, fetch current price from Gamma API.
      5. Compute PNL = (sold_usdc + open_value) - bought_usdc.
      6. Win rate = wins / (wins + losses).

    Open positions are valued at current market price.
    Positions with no remaining shares use realised proceeds only.
    Returns 0.0 if no positions can be evaluated.
    """
    from collections import defaultdict

    data = _req(_DATA_API + "/trades", {
        "user": address, "limit": 500, "offset": 0, "takerOnly": "false"
    })
    if not data or not isinstance(data, list):
        return 0.0

    # Build positions
    positions: dict = defaultdict(lambda: {
        "bought": 0.0, "sold": 0.0, "shares": 0.0,
        "cond_id": "", "oidx": 0, "last_buy_ts": 0,
    })
    for trade in data:
        side   = trade.get("side", "").upper()
        usdc   = float(trade.get("size", 0))
        price  = float(trade.get("price", 0.001))
        shares = usdc / max(price, 0.001)
        cid    = trade.get("conditionId", "")
        oidx   = int(trade.get("outcomeIndex", 0))
        ts     = int(trade.get("timestamp", 0))
        if not cid:
            continue
        p = positions[(cid, oidx)]
        p["cond_id"] = cid
        p["oidx"]    = oidx
        if side == "BUY":
            p["bought"] += usdc
            p["shares"] += shares
            if ts > p["last_buy_ts"]:
                p["last_buy_ts"] = ts
        elif side in ("SELL", "REDEEM"):
            p["sold"]   += usdc
            p["shares"] -= shares

    # Sample: most recently bought first, skip if no capital deployed
    sampled = sorted(
        [p for p in positions.values() if p["bought"] > 0],
        key=lambda x: x["last_buy_ts"],
        reverse=True,
    )[:sample]

    wins = losses = 0
    for pos in sampled:
        mkt = _req(_GAMMA_API + "/markets", {"condition_ids": pos["cond_id"]})
        time.sleep(_REQ_PAUSE)
        if not mkt:
            continue

        m = next(
            (x for x in mkt if x.get("conditionId") == pos["cond_id"]),
            mkt[0],
        ) if isinstance(mkt, list) else mkt

        prices_raw = m.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw)
            except Exception:
                continue
        else:
            prices = prices_raw or []

        try:
            px = float(prices[pos["oidx"]])
        except (IndexError, TypeError, ValueError):
            px = 0.5

        open_shares = max(pos["shares"], 0.0)
        open_val    = open_shares * px if open_shares > 0.01 else 0.0
        pnl         = (pos["sold"] + open_val) - pos["bought"]

        if pnl > 0:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    return (wins / total * 100) if total > 0 else 0.0


# ── Watchlist manager ─────────────────────────────────────────────────────────

class WatchlistManager:
    """
    Maintains a live, auto-refreshing list of traders to copy.

    Trader selection (each refresh):
      1. Fetch top (top_n × 6) traders from monthly PNL leaderboard.
      2. Walk them in PNL order, estimating win rate for each.
      3. Keep the first top_n traders whose win rate >= min_wr.
      4. Cache all qualifying addresses permanently (append-only).
      5. Push the new active list to bot.trader_addrs.

    Early exit: stops evaluating candidates once top_n are found, so
    we only check as many traders as needed — typically 5–15 for a 60% threshold.
    """

    def __init__(
        self,
        top_n: int         = 5,
        min_wr: float      = 60.0,
        refresh_hours: int = 6,
        log_fn             = None,
    ):
        self.top_n         = top_n
        self.min_wr        = min_wr
        self.refresh_hours = refresh_hours
        self._log          = log_fn or print
        self.cache         = AddressCache()
        self._lock         = threading.Lock()
        self._active: dict = {}     # name → proxyWallet (current live watchlist)
        self._last_refresh = 0.0
        self._bot          = None

    def start(self, bot):
        """
        Blocking initial load — populates bot.trader_addrs before returning.
        Then spawns a daemon thread for subsequent 6-hour refreshes.
        """
        self._bot = bot
        self._do_refresh()
        t = threading.Thread(target=self._refresh_loop, daemon=True)
        t.start()

    def get_active(self) -> dict:
        """Thread-safe snapshot of current {name: proxyWallet}."""
        with self._lock:
            return dict(self._active)

    def status_line(self) -> str:
        age_h = (time.time() - self._last_refresh) / 3600
        next_h = max(0.0, self.refresh_hours - age_h)
        with self._lock:
            n = len(self._active)
        return (
            f"Watchlist: {n} active  |  "
            f"cache: {len(self.cache)} total  |  "
            f"next refresh ~{next_h:.1f}h"
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _refresh_loop(self):
        interval = self.refresh_hours * 3600
        while True:
            time.sleep(interval)
            self._do_refresh()

    def _do_refresh(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._log(
            f"  [Watchlist] Refresh at {ts} "
            f"(top {self.top_n} by PNL, min WR {self.min_wr:.0f}%)..."
        )
        try:
            self._do_refresh_inner()
        except Exception as exc:
            self._log(
                f"  [Watchlist] ERROR during refresh: {exc!r} — "
                f"keeping existing list (last successful: "
                f"{self.cache.get_last_successful_refresh()})"
            )

    def _do_refresh_inner(self):
        # Fetch leaderboard — 6× headroom so we find top_n even with tight WR filter
        data = _req(_DATA_API + "/leaderboard", {
            "timePeriod": "MONTH", "orderBy": "PNL",
            "limit": self.top_n * 6,
        })
        if not data:
            self._log(
                f"  [Watchlist] Leaderboard fetch failed — keeping existing list "
                f"(last successful: {self.cache.get_last_successful_refresh()})"
            )
            return

        rows = data if isinstance(data, list) else data.get("data", [])
        candidates = []
        for row in rows:
            addr = row.get("proxyWallet") or row.get("address") or ""
            name = row.get("name") or row.get("userName") or (addr[:10] if addr else "")
            pnl  = float(row.get("pnl", 0))
            if addr and name:
                candidates.append({"name": name, "addr": addr, "pnl": pnl})

        # Re-sort by PNL descending to be explicit (API should already be sorted)
        candidates.sort(key=lambda x: x["pnl"], reverse=True)

        qualified = []
        checked   = 0
        for c in candidates:
            if len(qualified) >= self.top_n:
                break
            checked += 1
            wr = _estimate_win_rate(c["addr"])
            c["win_rate"] = wr
            passed = wr >= self.min_wr
            self._log(
                f"  [Watchlist]   {c['name']:<28}  "
                f"PNL ${c['pnl']:>10,.0f}  "
                f"WR {wr:5.1f}%  "
                f"{'PASS' if passed else 'skip'}"
            )
            if passed:
                qualified.append(c)
            time.sleep(0.2)

        if not qualified:
            self._log(
                f"  [Watchlist] No traders passed {self.min_wr}% WR filter "
                f"(checked {checked}) — keeping existing list "
                f"(last successful: {self.cache.get_last_successful_refresh()})"
            )
            return

        new_active = {c["name"]: c["addr"] for c in qualified}

        # Permanent cache update (append-only) + stamp successful refresh time
        self.cache.update(new_active)
        success_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.cache.set_last_successful_refresh(success_ts)
        self._last_refresh = time.time()

        # Log changes
        with self._lock:
            old_names = set(self._active.keys())
            new_names = set(new_active.keys())
            added     = new_names - old_names
            removed   = old_names - new_names
            self._active = new_active

        if added:
            self._log(f"  [Watchlist] Added   : {', '.join(sorted(added))}")
        if removed:
            self._log(
                f"  [Watchlist] Dropped : {', '.join(sorted(removed))} "
                f"(addresses kept in cache for open position resolution)"
            )

        # Push to bot
        if self._bot is not None:
            with self._bot.lock:
                self._bot.trader_addrs       = new_active
                self._bot.last_addr_refresh  = time.time()

        self._log(
            f"  [Watchlist] Active: {list(new_names)}  "
            f"| cache total: {len(self.cache)}  "
            f"| last_successful_refresh: {success_ts}"
        )
