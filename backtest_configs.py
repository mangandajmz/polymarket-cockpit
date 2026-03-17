"""
Polymarket Config Comparison Backtester  v2
============================================
Tests 4 watchlist configurations against 30 days of real data.

APIs used (no auth required):
  DATA_API  = https://data-api.polymarket.com/v1   (leaderboard, trades)
  GAMMA_API = https://gamma-api.polymarket.com      (market prices / resolution)

Configs:
  Baseline : majorexploiter + beachboy4             | $200 min whale
  Config A : top 10 monthly PNL, min 40% WR        | $200 min whale
  Config B : top 10 monthly PNL, min 40% WR        | $100 min whale
  Config C : top 5 by win rate (>= 50%)            | $100 min whale

PNL simulation:
  - Tracks each qualifying BUY trade individually (sorted by timestamp)
  - Bankroll starts at $100; each trade takes min(whale*0.10, remaining bankroll)
  - Bankroll is replenished when a position closes (simplified: at end of window)
  - "Uncapped copy PNL" also shown: sum(position_pnl * 0.10) without bankroll cap

Win rate:
  - Computed per-trader using current Gamma API prices for still-open positions
  - Positions with net_shares sold/redeemed use realised PNL
  - Sampled to first 40 positions per trader for speed (cached across configs)
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────
DATA_API      = "https://data-api.polymarket.com/v1"
GAMMA_API     = "https://gamma-api.polymarket.com"
COPY_RATIO    = 0.10          # 1/10th of whale trade size
LOOKBACK_DAYS = 30
REQ_PAUSE     = 0.35          # polite rate limit between API calls (seconds)
BANKROLL      = 100.0         # simulated starting bankroll ($)
WR_SAMPLE     = 40            # max positions to price-check per trader for win-rate

CRYPTO_KW = {
    "bitcoin","btc","ethereum","eth","crypto","solana","sol","xrp",
    "ripple","dogecoin","doge","bnb","binance","nft","blockchain",
    "defi","polygon","matic","avalanche","avax","chainlink","cardano",
    "ada","litecoin","ltc","usdc","usdt","stablecoin","web3","token",
}

# ── Utilities ─────────────────────────────────────────────────────────────────

def is_crypto(title: str) -> bool:
    return any(kw in title.lower() for kw in CRYPTO_KW)


def ts_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def ts_days_ago(days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())


def get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [WARN] GET {url} => {e}")
                return None
            time.sleep(1 + attempt)


# ── Leaderboard ───────────────────────────────────────────────────────────────

def fetch_leaderboard(period="MONTH", order="PNL", limit=50) -> list:
    data = get(f"{DATA_API}/leaderboard", {
        "timePeriod": period, "orderBy": order, "limit": limit
    })
    if not data:
        return []
    rows = data if isinstance(data, list) else data.get("data", [])
    out = []
    for row in rows:
        addr = row.get("proxyWallet") or row.get("address") or ""
        name = row.get("name") or row.get("userName") or (addr[:10] if addr else "unknown")
        pnl  = float(row.get("pnl", 0))
        vol  = float(row.get("volume", row.get("vol", 1)) or 1)
        out.append({"address": addr, "name": name, "pnl": pnl, "volume": vol, "trades": []})
    return out


# ── Trades ────────────────────────────────────────────────────────────────────

def fetch_trades(address: str, start_ts: int, end_ts: int, limit=500) -> list:
    """Fetch all trades for address within [start_ts, end_ts]."""
    all_trades = []
    offset = 0
    while True:
        data = get(f"{DATA_API}/trades", {
            "user": address, "limit": limit, "offset": offset, "takerOnly": "false"
        })
        time.sleep(REQ_PAUSE)
        if not data or not isinstance(data, list):
            break
        window = [t for t in data if start_ts <= int(t.get("timestamp", 0)) <= end_ts]
        all_trades.extend(window)
        oldest = int(data[-1].get("timestamp", 0)) if data else 0
        if len(data) < limit or oldest < start_ts:
            break
        offset += limit
    return all_trades


# ── Market Price (Gamma API, cached) ─────────────────────────────────────────

_price_cache: dict = {}
_price_fetch_count = 0


def fetch_market_price(cond_id: str, outcome_idx: int) -> tuple:
    """
    Returns (current_price_or_None, is_settled_bool).
    Uses resolution signal from bot: closed=True AND max(outcomePrices) >= 0.99.
    """
    global _price_fetch_count
    key = (cond_id, outcome_idx)
    if key in _price_cache:
        return _price_cache[key]

    data = get(f"{GAMMA_API}/markets", {"condition_ids": cond_id})
    _price_fetch_count += 1
    time.sleep(REQ_PAUSE)
    if not data:
        _price_cache[key] = (None, False)
        return None, False

    if isinstance(data, list):
        m = next((item for item in data if item.get("conditionId") == cond_id), data[0])
    else:
        m = data

    closed     = bool(m.get("closed", False))
    prices_raw = m.get("outcomePrices", "[]")
    if isinstance(prices_raw, str):
        try:
            prices = json.loads(prices_raw)
        except Exception:
            prices = []
    else:
        prices = prices_raw or []

    try:
        px = float(prices[outcome_idx])
    except (IndexError, TypeError, ValueError):
        px = None

    settled = False
    if closed and prices:
        try:
            if max(float(p) for p in prices) >= 0.99:
                settled = True
        except (ValueError, TypeError):
            pass

    _price_cache[key] = (px, settled)
    return px, settled


# ── Position Builder ──────────────────────────────────────────────────────────

def build_positions(trades: list) -> dict:
    """
    Aggregate trades into per-(conditionId, outcomeIdx) position dict.
    Tracks all individual BUY timestamps/sizes for bankroll simulation.
    """
    positions = defaultdict(lambda: {
        "bought_usdc":   0.0,
        "sold_usdc":     0.0,
        "net_shares":    0.0,
        "outcome_idx":   0,
        "title":         "",
        "condition_id":  "",
        "buys":          [],    # list of (timestamp, usdc, price) for qualifying-trade logic
    })

    for trade in trades:
        side        = trade.get("side", "").upper()
        usdc        = float(trade.get("size", 0))
        price       = float(trade.get("price", 0.001))
        shares      = usdc / max(price, 0.001)
        cond_id     = trade.get("conditionId", "")
        outcome_idx = int(trade.get("outcomeIndex", 0))
        title       = trade.get("title", cond_id[:20])
        ts          = int(trade.get("timestamp", 0))

        if not cond_id:
            continue

        key = (cond_id, outcome_idx)
        pos = positions[key]
        pos["outcome_idx"]  = outcome_idx
        pos["title"]        = title
        pos["condition_id"] = cond_id

        if side == "BUY":
            pos["bought_usdc"] += usdc
            pos["net_shares"]  += shares
            pos["buys"].append((ts, usdc, price))
        elif side in ("SELL", "REDEEM"):
            pos["sold_usdc"]  += usdc
            pos["net_shares"] -= shares

    return dict(positions)


# ── Win Rate (per trader, with price fetching) ────────────────────────────────

def compute_win_rate(trades: list, sample: int = WR_SAMPLE) -> float:
    """
    Compute win rate from actual resolved/priced positions.
    Samples up to `sample` positions to limit API calls.
    Prices are cached globally.
    """
    positions = build_positions(trades)
    if not positions:
        return 0.0

    # Sort by most-recently bought (largest timestamp first) for freshest sample
    def last_buy_ts(pos):
        return max((b[0] for b in pos["buys"]), default=0) if pos["buys"] else 0

    sampled = sorted(
        [p for p in positions.values() if p["bought_usdc"] > 0],
        key=last_buy_ts,
        reverse=True
    )[:sample]

    wins = losses = 0
    for pos in sampled:
        bought     = pos["bought_usdc"]
        sold       = pos["sold_usdc"]
        net_shares = pos["net_shares"]
        cond_id    = pos["condition_id"]
        oidx       = pos["outcome_idx"]

        if net_shares > 0.01:
            px, settled = fetch_market_price(cond_id, oidx)
            open_value  = net_shares * (px if px is not None else 0.5)
        else:
            open_value = 0.0

        pnl = (sold + open_value) - bought
        if pnl > 0:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    return (wins / total * 100) if total > 0 else 0.0


# ── Config Simulation ─────────────────────────────────────────────────────────

def simulate_config(
    all_trades: list,
    min_whale: float,
    bankroll: float = BANKROLL,
) -> dict:
    """
    Simulate copying all qualifying BUY trades from pooled trader trades.

    Qualifying trade = BUY, size >= min_whale, not a crypto market.

    Two PNL measures:
      uncapped_pnl : sum(position_pnl * COPY_RATIO) — "infinite capital" scaling
      bankroll_pnl : bankroll-aware simulation where each qualifying BUY is
                     executed as min(whale_size * COPY_RATIO, remaining_bankroll)
                     and the bankroll is credited when positions resolve.
    """
    positions = build_positions(all_trades)

    # ── 1. Identify qualifying trades and resolve positions ──────────────────
    # Flat list of all qualifying BUYs: (timestamp, cond_id, outcome_idx, usdc, price)
    qualifying_buys = []
    for pos in positions.values():
        if is_crypto(pos["title"]):
            continue
        for ts, usdc, price in pos["buys"]:
            if usdc >= min_whale:
                qualifying_buys.append((ts, pos["condition_id"], pos["outcome_idx"], usdc, price))

    qualifying_buys.sort(key=lambda x: x[0])  # chronological order
    qualifying_count = len(qualifying_buys)

    # ── 2. Value all positions (fetch prices, use cache) ────────────────────
    pos_value: dict = {}   # (cond_id, oidx) → {pnl, settled, current_price}
    for key, pos in positions.items():
        if is_crypto(pos["title"]):
            continue
        if not any(sz >= min_whale for _, sz, _ in pos["buys"]):
            continue

        bought     = pos["bought_usdc"]
        sold       = pos["sold_usdc"]
        net_shares = pos["net_shares"]
        cond_id    = pos["condition_id"]
        oidx       = pos["outcome_idx"]

        if net_shares > 0.01:
            px, settled = fetch_market_price(cond_id, oidx)
            open_value  = net_shares * (px if px is not None else 0.5)
        else:
            open_value = 0.0
            settled    = True
            px         = 0.0

        pnl = (sold + open_value) - bought
        pos_value[key] = {
            "pnl":            pnl,
            "settled":        settled,
            "bought_usdc":    bought,
            "current_price":  px,
            "win":            pnl > 0,
        }

    # ── 3. Uncapped PNL (position-level, no bankroll constraint) ─────────────
    uncapped_pnl = sum(v["pnl"] * COPY_RATIO for v in pos_value.values())

    # ── 4. Bankroll-aware PNL simulation ─────────────────────────────────────
    # Track per (cond_id, oidx): shares_bought and cost under bankroll constraint
    sim_positions: dict = defaultdict(lambda: {"cost": 0.0, "shares": 0.0})
    remaining_bk = bankroll

    for ts, cond_id, oidx, usdc, price in qualifying_buys:
        ideal_copy  = usdc * COPY_RATIO
        actual_copy = min(ideal_copy, remaining_bk)
        if actual_copy < 0.001:
            continue
        shares = actual_copy / max(price, 0.001)
        sim_positions[(cond_id, oidx)]["cost"]   += actual_copy
        sim_positions[(cond_id, oidx)]["shares"] += shares
        remaining_bk -= actual_copy

    # Calculate bankroll PNL from sim positions
    bk_pnl = 0.0
    for (cond_id, oidx), sp in sim_positions.items():
        if sp["cost"] == 0:
            continue
        pv = pos_value.get((cond_id, oidx))
        if pv is None:
            # Position wasn't in qualifying filter — value at 0.5
            px = 0.5
        else:
            px = pv["current_price"] if pv["current_price"] is not None else 0.5
        proceeds  = sp["shares"] * px
        bk_pnl   += proceeds - sp["cost"]

    final_bk = bankroll + bk_pnl

    # ── 5. Win / loss count ──────────────────────────────────────────────────
    wins = sum(1 for v in pos_value.values() if v["win"])
    losses = sum(1 for v in pos_value.values() if not v["win"])
    total  = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0.0

    return {
        "qualifying_trades": qualifying_count,
        "uncapped_pnl":      uncapped_pnl,
        "bankroll_pnl":      bk_pnl,
        "final_bankroll":    final_bk,
        "win_rate":          win_rate,
        "wins":              wins,
        "losses":            losses,
        "total_positions":   total,
    }


# ── Consistency Score ─────────────────────────────────────────────────────────

def consistency_score(traders: list, weekly_addrs: set) -> float:
    """Fraction of config traders also on the current weekly leaderboard."""
    if not traders:
        return 0.0
    in_both = sum(1 for t in traders if t["address"] in weekly_addrs)
    return in_both / len(traders) * 100


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 72)
    print("  Polymarket Config Comparison Backtester  v2")
    print(f"  Run date  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Data API  : {DATA_API}")
    print(f"  Gamma API : {GAMMA_API}")
    print(f"  Lookback  : {LOOKBACK_DAYS} days  |  Copy ratio : 1:{int(1/COPY_RATIO)}")
    print(f"  Bankroll  : ${BANKROLL:.0f}  |  Auth: none (public endpoints)")
    print("=" * 72)

    # ─── 1. Leaderboards ──────────────────────────────────────────────────────
    print("\n[1/6] Fetching monthly leaderboard (top 50 by PNL)...")
    monthly_lb = fetch_leaderboard("MONTH", "PNL", 50)
    print(f"      {len(monthly_lb)} entries. Top 5: "
          f"{', '.join(t['name'] for t in monthly_lb[:5])}")

    if not monthly_lb:
        sys.exit("[FATAL] Could not fetch leaderboard.")

    print("[1/6] Fetching weekly leaderboard (consistency baseline)...")
    weekly_lb    = fetch_leaderboard("WEEK", "PNL", 50)
    weekly_addrs = {t["address"] for t in weekly_lb}
    print(f"      {len(weekly_lb)} entries, {len(weekly_addrs)} unique addresses.")

    # Show monthly leaderboard
    print(f"\n  Monthly PNL leaderboard (top 20):")
    print(f"  {'Rank':<5} {'Name':<28} {'PNL':>12} {'Volume':>14}")
    print(f"  {'-'*62}")
    for i, t in enumerate(monthly_lb[:20], 1):
        print(f"  {i:<5} {t['name']:<28} ${t['pnl']:>11,.0f} ${t['volume']:>13,.0f}")

    # ─── 2. Resolve baseline traders ──────────────────────────────────────────
    BASELINE_NAMES = {"majorexploiter", "beachboy4"}
    baseline_traders = [t for t in monthly_lb if t["name"] in BASELINE_NAMES]
    found = {t["name"] for t in baseline_traders}
    for t in weekly_lb:
        if t["name"] in BASELINE_NAMES and t["name"] not in found:
            baseline_traders.append(t)
            found.add(t["name"])

    print(f"\n  Baseline traders: {[t['name'] for t in baseline_traders]}")
    missing = BASELINE_NAMES - found
    if missing:
        print(f"  [WARN] Not on leaderboard this period: {missing}")

    # ─── 3. Fetch trades for all candidates ───────────────────────────────────
    # We need top 50 monthly for Config C win-rate selection
    all_candidates: dict = {}
    for t in monthly_lb[:50]:
        all_candidates[t["address"]] = t
    for t in baseline_traders:
        all_candidates[t["address"]] = t

    start_ts = ts_days_ago(LOOKBACK_DAYS)
    end_ts   = ts_now()

    print(f"\n[2/6] Fetching {LOOKBACK_DAYS}-day trades for {len(all_candidates)} traders...")

    for i, (addr, t) in enumerate(all_candidates.items(), 1):
        print(f"      ({i:>2}/{len(all_candidates)}) {t['name']:<30}", end="", flush=True)
        t["trades"] = fetch_trades(addr, start_ts, end_ts)
        print(f" → {len(t['trades']):>4} trades")

    # ─── 4. Compute per-trader win rates (with price fetching, cached) ────────
    # Only fetch for top 20 monthly PNL candidates (needed for A/B selection)
    # + top 50 for Config C selection. Price cache shared across all.
    top50_addrs = [t["address"] for t in monthly_lb[:50]]

    print(f"\n[3/6] Computing win rates for {len(top50_addrs)} traders")
    print(f"      (fetches up to {WR_SAMPLE} market prices per trader — cached)")

    for i, addr in enumerate(top50_addrs, 1):
        t = all_candidates.get(addr)
        if not t:
            continue
        print(f"      ({i:>2}/{len(top50_addrs)}) {t['name']:<30}", end="", flush=True)
        wr = compute_win_rate(t.get("trades", []))
        t["computed_wr"] = wr
        flag = " ✓≥50%" if wr >= 50 else (" ✓≥40%" if wr >= 40 else "")
        print(f" WR {wr:5.1f}%  [{len(t.get('trades',[]))} trades]{flag}")

    # Also compute for baseline traders not in top 50
    for t in baseline_traders:
        if "computed_wr" not in t:
            wr = compute_win_rate(t.get("trades", []))
            t["computed_wr"] = wr

    print(f"\n  Total Gamma API price fetches so far: {_price_fetch_count}")

    # ─── 5. Assemble configs ──────────────────────────────────────────────────
    print("\n[4/6] Assembling trader lists for each config...")

    # Config A/B: top 10 monthly PNL with computed_wr >= 40%
    # Pull from top 30 by PNL to have enough headroom after WR filtering
    config_ab_candidates = [
        all_candidates[addr]
        for addr in [t["address"] for t in monthly_lb[:30]]
        if addr in all_candidates
        and all_candidates[addr].get("computed_wr", 0) >= 40.0
    ]
    config_ab_traders = config_ab_candidates[:10]

    # Config C: top 5 by computed_wr >= 50%, drawn from all top-50 PNL traders
    eligible_c = sorted(
        [all_candidates[addr]
         for addr in [t["address"] for t in monthly_lb[:50]]
         if addr in all_candidates
         and all_candidates[addr].get("computed_wr", 0) >= 50.0],
        key=lambda x: x.get("computed_wr", 0),
        reverse=True
    )
    config_c_traders = eligible_c[:5]

    configs = [
        {
            "label":     "Baseline",
            "desc":      "majorexploiter + beachboy4  |  $200 min",
            "traders":   baseline_traders,
            "min_whale": 200.0,
        },
        {
            "label":     "Config A",
            "desc":      "Top 10 PNL (≥40% WR)       |  $200 min",
            "traders":   config_ab_traders,
            "min_whale": 200.0,
        },
        {
            "label":     "Config B",
            "desc":      "Top 10 PNL (≥40% WR)       |  $100 min",
            "traders":   config_ab_traders,
            "min_whale": 100.0,
        },
        {
            "label":     "Config C",
            "desc":      "Top 5 by WR (≥50%)         |  $100 min",
            "traders":   config_c_traders,
            "min_whale": 100.0,
        },
    ]

    for cfg in configs:
        print(f"\n  {cfg['label']} — {cfg['desc']}")
        if cfg["traders"]:
            for t in cfg["traders"]:
                wr = t.get("computed_wr", 0)
                print(f"    - {t['name']:<30}  PNL ${t['pnl']:>10,.0f}  WR {wr:.1f}%")
        else:
            print(f"    - [no traders matched criteria]")

    # ─── 6. Run simulations ───────────────────────────────────────────────────
    print("\n[5/6] Running per-config simulations...")
    print("      (additional market price fetches will be needed — all cached)")

    results = []
    for cfg in configs:
        print(f"\n  >>> {cfg['label']}: {cfg['desc']}")

        pool = []
        for t in cfg["traders"]:
            pool.extend(all_candidates.get(t["address"], {}).get("trades", []))

        if not pool:
            print("      [WARN] No trades — all metrics will be zero.")
            m = {
                "qualifying_trades": 0, "uncapped_pnl": 0.0,
                "bankroll_pnl": 0.0, "final_bankroll": BANKROLL,
                "win_rate": 0.0, "wins": 0, "losses": 0, "total_positions": 0,
            }
        else:
            m = simulate_config(pool, cfg["min_whale"])

        consist = consistency_score(cfg["traders"], weekly_addrs)

        m["label"]        = cfg["label"]
        m["desc"]         = cfg["desc"]
        m["n_traders"]    = len(cfg["traders"])
        m["trader_names"] = [t["name"] for t in cfg["traders"]]
        m["consistency"]  = consist
        results.append(m)

        print(f"      Traders          : {m['n_traders']}")
        print(f"      Qualifying trades: {m['qualifying_trades']:,}")
        print(f"      Uncapped copy PNL: ${m['uncapped_pnl']:+,.2f}  "
              f"(sum of pos PNL × 0.10, no bankroll cap)")
        print(f"      Bankroll sim PNL : ${m['bankroll_pnl']:+,.2f}  "
              f"(${BANKROLL:.0f} → ${m['final_bankroll']:.2f})")
        print(f"      Win rate         : {m['win_rate']:.1f}%  "
              f"({m['wins']}W / {m['losses']}L from {m['total_positions']} positions)")
        print(f"      Consistency      : {consist:.0f}%  "
              f"of traders also on weekly leaderboard")

    # ─── 7. Comparison Table ─────────────────────────────────────────────────
    print("\n\n" + "=" * 82)
    print(f"  CONFIG COMPARISON TABLE  ({LOOKBACK_DAYS}-day backtest, ${BANKROLL:.0f} bankroll, 1:10 copy)")
    print("=" * 82)

    print(f"\n  {'Config':<11} {'Traders':>7}  {'Qual.Trades':>11}  "
          f"{'Uncapped PNL':>13}  {'Bk PNL':>9}  {'Win%':>6}  {'Consist%':>9}")
    print("  " + "-" * 72)

    best_uncapped = max(r["uncapped_pnl"] for r in results) if results else 0
    best_bk       = max(r["bankroll_pnl"] for r in results) if results else 0
    best_wr       = max(r["win_rate"]     for r in results) if results else 0

    for r in results:
        flags = []
        if r["uncapped_pnl"] == best_uncapped:
            flags.append("best PNL")
        if r["win_rate"] == best_wr and r["win_rate"] > 0:
            flags.append("best WR")
        if r["bankroll_pnl"] == best_bk:
            flags.append("best bk")
        flag_str = f"  ← {', '.join(flags)}" if flags else ""

        print(
            f"  {r['label']:<11} {r['n_traders']:>7}  "
            f"{r['qualifying_trades']:>11,}  "
            f"${r['uncapped_pnl']:>+12,.2f}  "
            f"${r['bankroll_pnl']:>+8,.2f}  "
            f"{r['win_rate']:>5.1f}%  "
            f"{r['consistency']:>8.0f}%"
            f"{flag_str}"
        )

    print("  " + "-" * 72)

    # ─── 8. Trader breakdown ──────────────────────────────────────────────────
    print("\n  TRADER LISTS:")
    for r in results:
        names = ", ".join(r["trader_names"]) or "[none — no traders met criteria]"
        print(f"  {r['label']:<11}: {names}")

    # ─── 9. Notes ─────────────────────────────────────────────────────────────
    print(f"""
  COLUMN NOTES:
  • Qual. Trades  = individual BUY actions ≥ min_whale, non-crypto markets only
  • Uncapped PNL  = Σ(position PNL × 0.10) — what you'd make at exact 1:10 ratio
                    with unlimited capital; best for comparing strategies fairly
  • Bk PNL        = bankroll-aware sim starting at ${BANKROLL:.0f}; each qualifying BUY
                    takes min(whale×0.10, remaining bankroll) in timestamp order
  • Win%          = % of (market × outcome) positions that are currently profitable
                    (open positions valued at live Gamma API price)
  • Consist%      = % of config traders appearing in BOTH monthly & weekly top-50
                    (proxy for leaderboard stability / trader staying power)
  • Gamma API fetches this run: {_price_fetch_count}
""")

    # ─── 10. Recommendation ───────────────────────────────────────────────────
    print("  RECOMMENDATION:")
    if not any(r["n_traders"] > 0 for r in results):
        print("  No configs had any traders — check leaderboard API response.")
    else:
        # Composite score: 40% uncapped PNL, 30% WR, 20% consistency, 10% trade volume
        max_uncapped = max(abs(r["uncapped_pnl"]) for r in results) or 1
        max_trades   = max(r["qualifying_trades"] for r in results) or 1

        def score(r):
            pnl_n  = r["uncapped_pnl"] / max_uncapped
            wr_n   = r["win_rate"] / (best_wr or 1)
            cs_n   = r["consistency"] / 100.0
            qt_n   = r["qualifying_trades"] / max_trades
            return 0.40 * pnl_n + 0.30 * wr_n + 0.20 * cs_n + 0.10 * qt_n

        ranked = sorted(results, key=score, reverse=True)
        best   = ranked[0]
        print(f"  Best balanced config : {best['label']} — {best['desc']}")
        print(f"  Score basis          : "
              f"PNL ${best['uncapped_pnl']:+,.2f}  |  "
              f"WR {best['win_rate']:.1f}%  |  "
              f"Consistency {best['consistency']:.0f}%  |  "
              f"Trades {best['qualifying_trades']:,}")
        print()
        print(f"  Ranking: ", end="")
        print("  >  ".join(f"{r['label']} ({score(r):+.2f})" for r in ranked))

    print("\n" + "=" * 82 + "\n")


if __name__ == "__main__":
    main()
