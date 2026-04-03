"""
force_resolve.py
================
One-off script to resolve all PENDING positions in paper_trades.csv
by querying the Polymarket CLOB API directly.

Run on the server:
    python3 force_resolve.py

It will:
1. Find all unique PENDING (condition_id, outcome_index) pairs
2. Query CLOB API for each
3. If closed=True: calculate PnL and mark WIN/LOSS
4. If closed=False: leave as PENDING (still live)
5. Write updated CSV and print a summary
"""

import csv, time, requests
from pathlib import Path
from collections import defaultdict

CSV_FILE   = Path("paper_trades.csv")
CLOB_API   = "https://clob.polymarket.com"
CSV_FIELDS = [
    "timestamp","trader","market","outcome","whale_side",
    "whale_size_usdc","our_size_usdc","price","copy_shares",
    "conviction","status","resolved_pnl","condition_id","outcome_index",
]

def get_market(cid):
    for attempt in range(3):
        try:
            r = requests.get(f"{CLOB_API}/markets/{cid}", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None

# ── Load CSV ──────────────────────────────────────────────────────────────────
rows = []
with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# ── Find unique PENDING positions and accumulate cost/shares ──────────────────
positions = {}   # (cid, oidx) -> {cost, shares, indices:[row indices]}
for i, row in enumerate(rows):
    if row.get("status") not in ("PENDING", "OPEN"):
        continue
    cid  = row.get("condition_id", "")
    oidx = int(row.get("outcome_index", 0))
    key  = (cid, oidx)
    if key not in positions:
        positions[key] = {
            "cid": cid, "oidx": oidx,
            "market": row.get("market", ""),
            "total_cost": 0.0, "total_shares": 0.0,
            "indices": []
        }
    p = positions[key]
    p["total_cost"]   += float(row.get("our_size_usdc", 0) or 0)
    p["total_shares"] += float(row.get("copy_shares", 0) or 0)
    p["indices"].append(i)

print(f"\nFound {len(positions)} unique PENDING positions in CSV\n")
print(f"{'Market':<50} {'Closed':>7} {'Winner':>7} {'Price':>7} {'PnL':>10} {'Result'}")
print("-" * 95)

resolved_count = 0
skipped_count  = 0
results = {}   # key -> (status, pnl)

for key, p in positions.items():
    cid, oidx = key
    data = get_market(cid)
    time.sleep(0.2)

    if not data:
        print(f"  {'[API ERROR]':<50} — skipping {cid[:20]}")
        skipped_count += 1
        continue

    closed  = bool(data.get("closed", False))
    tokens  = data.get("tokens", [])

    if not closed:
        print(f"  {p['market'][:48]:<50} {'NO':>7}  — still live, skipping")
        skipped_count += 1
        continue

    if not tokens or oidx >= len(tokens):
        print(f"  {p['market'][:48]:<50} {'YES':>7}  — no token at oidx={oidx}, skipping")
        skipped_count += 1
        continue

    token   = tokens[oidx]
    price   = float(token.get("price", 0))
    winner  = bool(token.get("winner", False))

    proceeds = p["total_shares"] * price
    pnl      = proceeds - p["total_cost"]
    status   = "WIN" if pnl >= 0 else "LOSS"

    print(f"  {p['market'][:48]:<50} {'YES':>7} {str(winner):>7} {price:>7.4f} {pnl:>+10.4f}  {status}")
    results[key] = (status, pnl)
    resolved_count += 1

# ── Apply results to CSV rows ─────────────────────────────────────────────────
for key, (status, pnl) in results.items():
    cid, oidx = key
    for i in positions[key]["indices"]:
        rows[i]["status"]       = status
        rows[i]["resolved_pnl"] = f"{pnl:+.4f}"

# ── Write updated CSV ─────────────────────────────────────────────────────────
backup = CSV_FILE.with_name("paper_trades_pre_force_resolve.csv")
import shutil
shutil.copy2(CSV_FILE, backup)
print(f"\nBackup written: {backup}")

with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    w.writeheader()
    w.writerows(rows)

print(f"\n{'='*60}")
print(f"  Resolved : {resolved_count} positions")
print(f"  Skipped  : {skipped_count} (still live or API error)")
print(f"  CSV      : {CSV_FILE.resolve()}")
print(f"{'='*60}")
print("\nNext: sudo systemctl restart polymarket-bot")
