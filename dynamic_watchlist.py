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
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from api_client import JsonApiClient

# ── Module-level API config ───────────────────────────────────────────────────
_DATA_API   = "https://data-api.polymarket.com/v1"
_CLOB_API   = "https://clob.polymarket.com"
_WR_SAMPLE  = 10    # positions to price per candidate (speed vs accuracy trade-off)
_REQ_PAUSE  = 0.25  # seconds between CLOB API calls during WR estimation
_LEADERBOARD_HEADROOM_MULT = 12
_MIN_LEADERBOARD_CANDIDATES = 60


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


_RECENT_ACTIVITY_HOURS = _env_float("WATCHLIST_RECENT_ACTIVITY_HOURS", 24.0)

CACHE_FILE  = Path("watchlist_cache.json")

# Traders permanently excluded from the watchlist regardless of performance.
# Add names in lowercase. These traders will never be copied.
TRADER_BLOCKLIST = {
    "cemeterysun",  # 20% win rate, -$81.35 net, excluded 2026-04-01
}


# ── HTTP helper ───────────────────────────────────────────────────────────────

_HTTP = JsonApiClient(default_timeout=15.0, default_retries=3, backoff_base=1.0, jitter_max=0.25)


def _req(url, params=None, retries=3):
    return _HTTP.get_json(url, params=params, retries=retries)


def _valid_addr(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("0x") and len(addr) >= 10


def _fetch_market_price(condition_id: str, outcome_index: int) -> float | None:
    data = _req(f"{_CLOB_API}/markets/{condition_id}")
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens") or []
    if outcome_index < 0 or outcome_index >= len(tokens):
        return None
    try:
        return float(tokens[outcome_index].get("price", 0.0))
    except (TypeError, ValueError):
        return None


def _latest_trade_summary(address: str, now_ts: int | None = None) -> dict:
    now_ts = int(time.time()) if now_ts is None else now_ts
    data = _req(_DATA_API + "/trades", {
        "user": address, "limit": 50, "offset": 0, "takerOnly": "false"
    })
    summary = {
        "latest_trade_ts": 0,
        "latest_trade_age_h": None,
        "latest_trade_side": None,
        "latest_trade_usdc": None,
        "latest_trade_title": None,
    }
    if not isinstance(data, list):
        return summary

    latest = None
    latest_ts = 0
    for trade in data:
        try:
            ts = int(trade.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            continue
        if ts > latest_ts:
            latest_ts = ts
            latest = trade

    if latest is None:
        return summary

    try:
        usdc = float(latest.get("usdcSize") or latest.get("size") or 0)
    except (TypeError, ValueError):
        usdc = 0.0

    summary.update({
        "latest_trade_ts": latest_ts,
        "latest_trade_age_h": max(0.0, (now_ts - latest_ts) / 3600),
        "latest_trade_side": latest.get("side"),
        "latest_trade_usdc": usdc,
        "latest_trade_title": latest.get("title"),
    })
    return summary


# ── Permanent address cache ───────────────────────────────────────────────────

class AddressCache:
    """
    Append-only store of {trader_name: {"address": proxyWallet, "active": bool}}.

    Entries are written the first time a trader qualifies for the watchlist
    and are never removed. This ensures that:
      - A trader who drops off the leaderboard mid-month can still have their
        open positions resolved by the bot's resolution loop.
      - The bot never loses track of an address it has already acted on.

    Cache format (v2):
      Each trader entry is a dict: {"address": "0x...", "active": true/false}.
      The "active" flag is True only for traders in the current top-N selection.
      Old string-valued entries are migrated to {"address": str, "active": False}
      on load for backward compatibility.

    Meta keys are prefixed with "_" and stored alongside trader entries.
    """

    def __init__(self, path: Path = CACHE_FILE):
        self.path   = path
        self._data: dict = {}   # name -> {"address": str, "active": bool}
        self._meta: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._meta = {k: v for k, v in raw.items() if k.startswith("_")}
                self._data = {}
                for k, v in raw.items():
                    if k.startswith("_"):
                        continue
                    if isinstance(v, dict):
                        self._data[k] = {
                            "address": v.get("address", ""),
                            "active":  bool(v.get("active", False)),
                        }
                    elif isinstance(v, str):
                        # old flat-string format — treat as inactive until next refresh
                        self._data[k] = {"address": v, "active": False}
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

    def set_active_traders(self, active_names: set):
        """Mark traders in active_names as active=True, all others as active=False."""
        for name in self._data:
            self._data[name]["active"] = (name in active_names)
        self._save()

    def update(self, entries: dict):
        """Merge new {name: addr} pairs. Existing entries are never overwritten."""
        changed = False
        for name, addr in entries.items():
            if name not in self._data and addr:
                self._data[name] = {"address": addr, "active": False}
                changed = True
        if changed:
            self._save()

    def get_all(self) -> dict:
        """Return {name: address} for all cached traders."""
        return {name: val["address"] for name, val in self._data.items()}

    def get_active(self) -> dict:
        """Return {name: address} for currently active traders only."""
        return {
            name: val["address"]
            for name, val in self._data.items()
            if val.get("active", False)
        }

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
        usdc   = float(trade.get("usdcSize") or trade.get("size") or 0)
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
        px = _fetch_market_price(pos["cond_id"], pos["oidx"])
        time.sleep(_REQ_PAUSE)
        if px is None:
            continue

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
      1. Fetch top monthly-PNL traders with enough headroom for activity skips.
      2. Skip traders whose latest trade is older than the recent-activity gate.
      3. Walk active traders in PNL order, estimating win rate for each.
      4. Keep the first top_n traders whose win rate >= min_wr.
      5. Cache all qualifying addresses permanently (append-only).
      6. Push the new active list to bot.trader_addrs.
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
        self._log(f"Trader blocklist active: {TRADER_BLOCKLIST}")
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

    def _write_health(self, **extra):
        if self._bot is None or not hasattr(self._bot, "store"):
            return
        payload = {
            "active_names": sorted(self.get_active().keys()),
            "active_count": len(self.get_active()),
            "cache_total": len(self.cache),
            "last_successful_refresh": self.cache.get_last_successful_refresh(),
        }
        payload.update(extra)
        self._bot.store.set_value("watchlist_health", payload)

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
            self._write_health(last_error=repr(exc))
            self._log(
                f"  [Watchlist] ERROR during refresh: {exc!r} — "
                f"keeping existing list (last successful: "
                f"{self.cache.get_last_successful_refresh()})"
            )

    def _do_refresh_inner(self):
        # Fetch extra headroom so inactive top-PNL wallets do not crowd out
        # active traders who can produce paper evidence today.
        limit = max(_MIN_LEADERBOARD_CANDIDATES, self.top_n * _LEADERBOARD_HEADROOM_MULT)
        data = _req(_DATA_API + "/leaderboard", {
            "timePeriod": "MONTH", "orderBy": "PNL",
            "limit": limit,
        })
        if not data:
            self._write_health(last_error="leaderboard_fetch_failed")
            self._log(
                f"  [Watchlist] Leaderboard fetch failed — keeping existing list "
                f"(last successful: {self.cache.get_last_successful_refresh()})"
            )
            return

        rows = data if isinstance(data, list) else data.get("data", [])
        candidates = []
        seen_addrs = set()
        for row in rows:
            addr = row.get("proxyWallet") or row.get("address") or ""
            name = row.get("name") or row.get("userName") or (addr[:10] if addr else "")
            try:
                pnl = float(row.get("pnl", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not _valid_addr(addr) or not isinstance(name, str) or not name.strip():
                continue
            addr_key = addr.lower()
            if addr_key in seen_addrs:
                continue
            seen_addrs.add(addr_key)
            candidates.append({"name": name.strip(), "addr": addr, "pnl": pnl})

        if not candidates:
            self._write_health(last_error="no_valid_candidates")
            self._log(
                "  [Watchlist] Leaderboard payload had no valid trader candidates — "
                "keeping existing list"
            )
            return

        # Re-sort by PNL descending to be explicit (API should already be sorted)
        candidates.sort(key=lambda x: x["pnl"], reverse=True)

        qualified = []
        checked   = 0
        skipped_inactive = 0
        now_ts = int(time.time())
        for c in candidates:
            if c.get("name", "").lower() in TRADER_BLOCKLIST:
                continue  # permanently blocked
            if len(qualified) >= self.top_n:
                break
            activity = _latest_trade_summary(c["addr"], now_ts=now_ts)
            c.update(activity)
            age_h = c.get("latest_trade_age_h")
            if age_h is None or age_h > _RECENT_ACTIVITY_HOURS:
                skipped_inactive += 1
                age_text = "never" if age_h is None else f"{age_h:.1f}h"
                self._log(
                    f"  [Watchlist]   {c['name']:<28}  "
                    f"PNL ${c['pnl']:>10,.0f}  "
                    f"latest {age_text:>8}  inactive"
                )
                time.sleep(0.2)
                continue
            checked += 1
            wr = _estimate_win_rate(c["addr"])
            c["win_rate"] = wr
            passed = wr >= self.min_wr
            self._log(
                f"  [Watchlist]   {c['name']:<28}  "
                f"PNL ${c['pnl']:>10,.0f}  "
                f"latest {age_h:>5.1f}h  "
                f"WR {wr:5.1f}%  "
                f"{'PASS' if passed else 'skip'}"
            )
            if passed:
                qualified.append(c)
            time.sleep(0.2)

        if not qualified:
            self._write_health(last_error="no_qualified_traders")
            self._log(
                f"  [Watchlist] No traders passed {self.min_wr}% WR filter "
                f"(checked {checked}, skipped inactive {skipped_inactive}) — keeping existing list "
                f"(last successful: {self.cache.get_last_successful_refresh()})"
            )
            return

        with self._lock:
            current_count = len(self._active)
        min_required = self.top_n if current_count == 0 else max(1, min(current_count, self.top_n))
        if len(qualified) < min_required:
            self._write_health(
                last_error="insufficient_qualified_traders",
                qualified_count=len(qualified),
                min_required=min_required,
                skipped_inactive=skipped_inactive,
                recent_activity_hours=_RECENT_ACTIVITY_HOURS,
            )
            self._log(
                f"  [Watchlist] Only {len(qualified)} qualified trader(s); "
                f"need at least {min_required} to replace current active set — keeping existing list"
            )
            return

        new_active = {c["name"]: c["addr"] for c in qualified}

        # Permanent cache update (append-only) + mark active/inactive + timestamp
        self.cache.update(new_active)
        self.cache.set_active_traders(set(new_active.keys()))
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
            self._write_health(last_error="")

        self._log(
            f"  [Watchlist] Active: {list(new_names)}  "
            f"| cache total: {len(self.cache)}  "
            f"| last_successful_refresh: {success_ts}"
        )
