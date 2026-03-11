"""
Polymarket Copy-Trade Backtester
=================================
Pulls top 5 ROI traders from the leaderboard (last 30 days),
simulates copying their trades at a 10:1 ratio, and reports metrics.

No authentication required — uses public Polymarket Data + Gamma APIs.
"""

import requests
import time
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_API   = "https://data-api.polymarket.com/v1"
GAMMA_API  = "https://gamma-api.polymarket.com"

COPY_RATIO   = 0.10          # 10:1 → we trade 1/10 of the original size
LOOKBACK     = 30            # days
TOP_N        = 5             # traders to copy
REQUEST_PAUSE = 0.3          # seconds between API calls (polite rate limiting)

# ── Helpers ──────────────────────────────────────────────────────────────────

def ts_now():
    return int(datetime.now(timezone.utc).timestamp())

def ts_days_ago(days):
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

def fmt_usdc(v):
    return f"${v:+,.2f}" if v != 0 else "$0.00"

def fmt_pct(v):
    return f"{v:+.1f}%"

def get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [WARN] GET {url} failed: {e}")
                return None
            time.sleep(1)

# ── Step 1: Leaderboard ───────────────────────────────────────────────────────

def fetch_leaderboard(top_n=TOP_N):
    print(f"\n{'='*60}")
    print(f"  STEP 1 — Fetching top {top_n} traders (last 30 days, by PnL)")
    print(f"{'='*60}")

    data = get(f"{DATA_API}/leaderboard", {
        "timePeriod": "MONTH",
        "orderBy":    "PNL",
        "limit":      top_n * 2,   # grab extra in case some have no trades
        "offset":     0,
    })

    if not data:
        sys.exit("Could not fetch leaderboard. Check network/API.")

    # The response may be a list directly or wrapped in a key
    rows = data if isinstance(data, list) else data.get("data", data.get("leaderboard", []))

    traders = []
    for row in rows:
        addr   = row.get("proxyWallet") or row.get("address") or row.get("proxy_wallet")
        name   = row.get("name") or row.get("userName") or addr[:10]
        pnl    = float(row.get("pnl", 0))
        volume = float(row.get("vol", row.get("volume", 0)))
        roi    = (pnl / volume * 100) if volume > 0 else 0
        traders.append({"address": addr, "name": name, "pnl": pnl,
                        "volume": volume, "roi": roi})

    # Sort by PnL descending, take top_n
    traders = sorted(traders, key=lambda x: x["pnl"], reverse=True)[:top_n]

    print(f"\n  {'Rank':<5} {'Name':<25} {'30d PnL':>12} {'Volume':>14} {'ROI':>8}")
    print(f"  {'-'*68}")
    for i, t in enumerate(traders, 1):
        print(f"  {i:<5} {t['name']:<25} {fmt_usdc(t['pnl']):>12} "
              f"${t['volume']:>13,.0f} {fmt_pct(t['roi']):>8}")

    return traders

# ── Step 2: Fetch Trades ──────────────────────────────────────────────────────

def fetch_trades(address, start_ts, end_ts, limit=500):
    """Pull all trades for an address in the time window."""
    all_trades = []
    offset = 0
    while True:
        data = get(f"{DATA_API}/trades", {
            "user":       address,
            "limit":      limit,
            "offset":     offset,
            "takerOnly":  "false",
        })
        time.sleep(REQUEST_PAUSE)

        if not data or not isinstance(data, list) or len(data) == 0:
            break

        # Filter to our time window
        window = [t for t in data
                  if start_ts <= int(t.get("timestamp", 0)) <= end_ts]
        all_trades.extend(window)

        if len(data) < limit:
            break
        if int(data[-1].get("timestamp", 0)) < start_ts:
            break
        offset += limit

    return all_trades

def fetch_all_trader_trades(traders):
    start = ts_days_ago(LOOKBACK)
    end   = ts_now()

    print(f"\n{'='*60}")
    print(f"  STEP 2 — Fetching trades ({LOOKBACK}-day window)")
    print(f"{'='*60}\n")

    for t in traders:
        trades = fetch_trades(t["address"], start, end)
        t["trades"] = trades
        print(f"  {t['name']:<25}  {len(trades):>4} trades fetched")

    return traders

# ── Step 3: Fetch Current Market Prices for Open Positions ───────────────────

def fetch_market_price(condition_id, outcome_index):
    """Get current price of a specific outcome from Gamma API."""
    data = get(f"{GAMMA_API}/markets", {"condition_ids": condition_id})
    time.sleep(REQUEST_PAUSE)
    if not data:
        return None, None
    market = data[0] if isinstance(data, list) else data
    outcomes = market.get("outcomes", "[]")
    prices   = market.get("outcomePrices", "[]")
    resolved = market.get("resolved", False)
    resolution = market.get("resolution", None)
    closed   = market.get("closed", False)

    # Parse if stringified JSON
    if isinstance(outcomes, str):
        import json
        try:
            outcomes = json.loads(outcomes)
            prices   = json.loads(prices)
        except Exception:
            return None, resolved

    try:
        current_price = float(prices[outcome_index])
    except (IndexError, TypeError, ValueError):
        current_price = None

    # If resolved, final price is 1.0 (winner) or 0.0 (loser)
    if resolved and resolution is not None:
        try:
            res_idx = outcomes.index(str(resolution))
            current_price = 1.0 if res_idx == outcome_index else 0.0
        except (ValueError, AttributeError):
            pass

    return current_price, resolved

# ── Step 4: Simulate Copy Trades & Calculate PnL ─────────────────────────────

def compute_trader_pnl(trader):
    """
    For each (conditionId, outcomeIndex) pair:
      - Sum up all USDC spent (BUY)
      - Sum up all USDC received (SELL)
      - Track remaining share balance
      - Value open shares at current market price
    Returns list of closed-trade dicts and overall stats.
    """
    # positions[key] = {bought_usdc, sold_usdc, net_shares, outcome_idx, title, outcome_label}
    positions = defaultdict(lambda: {
        "bought_usdc": 0.0,
        "sold_usdc":   0.0,
        "net_shares":  0.0,
        "outcome_idx": 0,
        "title":       "",
        "outcome":     "",
        "condition_id":"",
        "trades":      [],
    })

    for trade in trader["trades"]:
        side        = trade.get("side", "").upper()
        # API returns 'size' as the USDC notional value of the trade
        usdc        = float(trade.get("size", 0))
        price       = float(trade.get("price", 0))
        # Derive share count from USDC / price
        shares      = usdc / max(price, 0.001)
        cond_id     = trade.get("conditionId", "")
        outcome_idx = int(trade.get("outcomeIndex", 0))
        title       = trade.get("title", cond_id[:20])
        outcome     = trade.get("outcome", str(outcome_idx))
        ts          = int(trade.get("timestamp", 0))

        key = (cond_id, outcome_idx)
        pos = positions[key]
        pos["condition_id"] = cond_id
        pos["outcome_idx"]  = outcome_idx
        pos["title"]        = title
        pos["outcome"]      = outcome

        if side == "BUY":
            pos["bought_usdc"] += usdc
            pos["net_shares"]  += shares
        elif side in ("SELL", "REDEEM"):
            pos["sold_usdc"]   += usdc
            pos["net_shares"]  -= shares

        pos["trades"].append({
            "side": side, "usdc": usdc, "shares": shares,
            "price": price, "ts": ts,
        })

    # Value open positions at current market price
    trade_results = []
    total_open_value = 0.0

    cached_prices = {}
    for key, pos in positions.items():
        cond_id     = pos["condition_id"]
        outcome_idx = pos["outcome_idx"]
        bought      = pos["bought_usdc"]
        sold        = pos["sold_usdc"]
        net_shares  = pos["net_shares"]

        open_value  = 0.0
        is_resolved = False

        if net_shares > 0.01:  # Has open shares
            cache_key = (cond_id, outcome_idx)
            if cache_key not in cached_prices:
                current_price, resolved = fetch_market_price(cond_id, outcome_idx)
                cached_prices[cache_key] = (current_price, resolved)
            current_price, is_resolved = cached_prices[cache_key]
            if current_price is not None:
                open_value = net_shares * current_price
            else:
                open_value = net_shares * 0.5  # assume 50¢ if price unavailable

        pnl        = (sold + open_value) - bought
        scaled_pnl = pnl * COPY_RATIO
        scaled_buy = bought * COPY_RATIO
        win        = pnl > 0

        trade_results.append({
            "title":       pos["title"],
            "outcome":     pos["outcome"],
            "condition_id": cond_id,
            "bought_usdc": bought,
            "sold_usdc":   sold,
            "open_value":  open_value,
            "pnl":         pnl,
            "scaled_pnl":  scaled_pnl,
            "scaled_buy":  scaled_buy,
            "win":         win,
            "is_open":     net_shares > 0.01 and not is_resolved,
            "n_trades":    len(pos["trades"]),
            "roi_pct":     (pnl / bought * 100) if bought > 0 else 0,
        })

    return trade_results

# ── Step 5: Portfolio Metrics ─────────────────────────────────────────────────

def portfolio_metrics(trade_results):
    """Compute total PnL, win rate, max drawdown, best/worst trade."""
    if not trade_results:
        return {}

    # Sort by first trade timestamp (approximate with condition_id order)
    pnls = [t["scaled_pnl"] for t in trade_results]

    total_pnl    = sum(pnls)
    wins         = sum(1 for t in trade_results if t["win"])
    win_rate     = wins / len(trade_results) * 100 if trade_results else 0
    total_risked = sum(t["scaled_buy"] for t in trade_results)

    # Max drawdown: running cumulative PnL, find largest peak-to-trough
    cumulative = []
    running = 0
    for p in pnls:
        running += p
        cumulative.append(running)

    peak = 0
    max_dd = 0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    best  = max(trade_results, key=lambda t: t["scaled_pnl"])
    worst = min(trade_results, key=lambda t: t["scaled_pnl"])

    return {
        "total_pnl":    total_pnl,
        "total_risked": total_risked,
        "roi_pct":      (total_pnl / total_risked * 100) if total_risked > 0 else 0,
        "win_rate":     win_rate,
        "wins":         wins,
        "total_trades": len(trade_results),
        "max_drawdown": max_dd,
        "best":         best,
        "worst":        worst,
    }

# ── Step 6: Report ────────────────────────────────────────────────────────────

def print_report(traders):
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS — Copy Trading at {int(1/COPY_RATIO)}:1 Ratio")
    print(f"  Period: last {LOOKBACK} days  |  Top {TOP_N} traders by PnL")
    print(f"{'='*60}")

    all_metrics = []

    for t in traders:
        if not t.get("trades"):
            print(f"\n  {t['name']}: No trades in window — skipping.")
            continue

        results = compute_trader_pnl(t)
        if not results:
            print(f"\n  {t['name']}: Could not compute PnL — skipping.")
            continue

        m = portfolio_metrics(results)
        m["trader"] = t["name"]
        m["address"] = t["address"]
        all_metrics.append(m)

        print(f"\n  -- {t['name']} ({t['address'][:10]}...) --")
        print(f"     Markets traded : {m['total_trades']}")
        print(f"     Copy total PnL : {fmt_usdc(m['total_pnl'])} "
              f"(ROI {fmt_pct(m['roi_pct'])})")
        print(f"     Win rate       : {m['win_rate']:.1f}%  "
              f"({m['wins']}/{m['total_trades']} wins)")
        print(f"     Capital risked : {fmt_usdc(m['total_risked'])}")
        print(f"     Max drawdown   : {fmt_usdc(m['max_drawdown'])}")
        if m.get("best"):
            b = m["best"]
            print(f"     Best trade     : {b['title'][:40]} → {fmt_usdc(b['scaled_pnl'])}")
        if m.get("worst"):
            w = m["worst"]
            print(f"     Worst trade    : {w['title'][:40]} → {fmt_usdc(w['scaled_pnl'])}")

    # ── Aggregate Summary ───────────────────────────────────────────────────
    if not all_metrics:
        print("\n  No valid trader data found.")
        return

    print(f"\n{'='*60}")
    print(f"  AGGREGATE COPY-PORTFOLIO SUMMARY (all {TOP_N} traders combined)")
    print(f"{'='*60}")

    agg_pnl      = sum(m["total_pnl"]    for m in all_metrics)
    agg_risked   = sum(m["total_risked"] for m in all_metrics)
    agg_trades   = sum(m["total_trades"] for m in all_metrics)
    agg_wins     = sum(m["wins"]         for m in all_metrics)
    agg_wr       = agg_wins / agg_trades * 100 if agg_trades > 0 else 0
    agg_roi      = agg_pnl / agg_risked * 100 if agg_risked > 0 else 0
    agg_dd       = max(m["max_drawdown"] for m in all_metrics)  # worst single

    print(f"\n  Total PnL         : {fmt_usdc(agg_pnl)}  (ROI {fmt_pct(agg_roi)})")
    print(f"  Win Rate          : {agg_wr:.1f}%  ({agg_wins}/{agg_trades})")
    print(f"  Capital Risked    : {fmt_usdc(agg_risked)}")
    print(f"  Worst Drawdown    : {fmt_usdc(agg_dd)}")

    best_trader = max(all_metrics, key=lambda m: m["total_pnl"])
    worst_trader = min(all_metrics, key=lambda m: m["total_pnl"])
    print(f"\n  Most profitable to copy : {best_trader['trader']} → "
          f"{fmt_usdc(best_trader['total_pnl'])} ({fmt_pct(best_trader['roi_pct'])} ROI)")
    print(f"  Least profitable        : {worst_trader['trader']} → "
          f"{fmt_usdc(worst_trader['total_pnl'])} ({fmt_pct(worst_trader['roi_pct'])} ROI)")

    # ── Trader Ranking ──────────────────────────────────────────────────────
    print(f"\n  {'Rank':<5} {'Trader':<25} {'Copy PnL':>12} {'ROI':>8} {'Win%':>7}")
    print(f"  {'-'*62}")
    ranked = sorted(all_metrics, key=lambda m: m["total_pnl"], reverse=True)
    for i, m in enumerate(ranked, 1):
        print(f"  {i:<5} {m['trader']:<25} {fmt_usdc(m['total_pnl']):>12} "
              f"{fmt_pct(m['roi_pct']):>8} {m['win_rate']:>6.1f}%")

    print(f"\n{'='*60}")
    print(f"  Note: 'copy PnL' = original trader PnL × {COPY_RATIO:.2f} (10:1 ratio)")
    print(f"  Open positions are valued at current Polymarket price.")
    print(f"{'='*60}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n  Polymarket Copy-Trade Backtester")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    traders = fetch_leaderboard(top_n=TOP_N)
    traders = fetch_all_trader_trades(traders)

    print(f"\n{'='*60}")
    print(f"  STEP 3 — Valuing positions & computing PnL")
    print(f"  (fetching current prices for open positions…)")
    print(f"{'='*60}")

    print_report(traders)


if __name__ == "__main__":
    main()
