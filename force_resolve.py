"""
force_resolve.py
================
One-off script to resolve all open paper positions in paper_trades.csv
by querying the Polymarket CLOB API directly.

Run on the server:
    python3 force_resolve.py

It will:
1. Find all unique open positions
2. Query CLOB API for each
3. If closed=True: calculate PnL and mark WIN/LOSS
4. If closed=False: leave as PENDING/OPEN
5. Write updated CSV and print a summary
"""

from __future__ import annotations

import csv
import shutil
import time
from pathlib import Path

import requests

CSV_FILE = Path("paper_trades.csv")
CLOB_API = "https://clob.polymarket.com"
DEFAULT_FIELDS = [
    "timestamp", "trader", "market", "outcome", "whale_side",
    "whale_size_usdc", "our_size_usdc", "price", "copy_shares",
    "conviction", "status", "resolved_pnl", "condition_id", "outcome_index",
    "event_id", "position_id",
]


def get_market(cid: str) -> dict | None:
    for attempt in range(3):
        try:
            response = requests.get(f"{CLOB_API}/markets/{cid}", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def load_rows(csv_file: Path) -> tuple[list[dict], list[str]]:
    with open(csv_file, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or DEFAULT_FIELDS)
    for required in DEFAULT_FIELDS:
        if required not in fieldnames:
            fieldnames.append(required)
    return rows, fieldnames


def position_key(row: dict) -> tuple[str, str, int]:
    trader = row.get("trader", "unknown")
    cid = row.get("condition_id", "")
    oidx = int(row.get("outcome_index", 0) or 0)
    pos_id = row.get("position_id") or f"{trader}|{cid}|{oidx}"
    return pos_id, cid, oidx


def build_pending_positions(rows: list[dict]) -> dict[tuple[str, str, int], dict]:
    positions: dict[tuple[str, str, int], dict] = {}
    for index, row in enumerate(rows):
        if row.get("status") not in ("PENDING", "OPEN"):
            continue
        pos_id, cid, oidx = position_key(row)
        key = (pos_id, cid, oidx)
        if key not in positions:
            positions[key] = {
                "position_id": pos_id,
                "cid": cid,
                "oidx": oidx,
                "trader": row.get("trader", "unknown"),
                "market": row.get("market", ""),
                "total_cost": 0.0,
                "total_shares": 0.0,
                "indices": [],
            }
        bucket = positions[key]
        bucket["total_cost"] += float(row.get("our_size_usdc", 0) or 0)
        bucket["total_shares"] += float(row.get("copy_shares", 0) or 0)
        bucket["indices"].append(index)
    return positions


def resolve_positions(positions: dict[tuple[str, str, int], dict]) -> tuple[dict, int]:
    resolved_count = 0
    results = {}

    print(f"\nFound {len(positions)} unique open positions in CSV\n")
    print(f"{'Trader':<20} {'Market':<42} {'Closed':>7} {'Winner':>7} {'Price':>7} {'PnL':>10} {'Result'}")
    print("-" * 116)

    for key, pos in positions.items():
        _, cid, oidx = key
        data = get_market(cid)
        time.sleep(0.2)

        if not data:
            print(f"{pos['trader'][:20]:<20} {'[API ERROR]':<42} {'-':>7} {'-':>7} {'-':>7} {'-':>10} skip")
            continue

        closed = bool(data.get("closed", False))
        tokens = data.get("tokens", [])

        if not closed:
            print(f"{pos['trader'][:20]:<20} {pos['market'][:42]:<42} {'NO':>7} {'-':>7} {'-':>7} {'-':>10} skip")
            continue

        if not tokens or oidx >= len(tokens):
            print(f"{pos['trader'][:20]:<20} {pos['market'][:42]:<42} {'YES':>7} {'-':>7} {'-':>7} {'-':>10} skip")
            continue

        token = tokens[oidx]
        price = float(token.get("price", 0) or 0)
        winner = bool(token.get("winner", False))
        proceeds = pos["total_shares"] * price
        pnl = proceeds - pos["total_cost"]
        status = "WIN" if pnl >= 0 else "LOSS"

        print(
            f"{pos['trader'][:20]:<20} {pos['market'][:42]:<42} "
            f"{'YES':>7} {str(winner):>7} {price:>7.4f} {pnl:>+10.4f} {status}"
        )
        results[key] = (status, pnl)
        resolved_count += 1

    return results, resolved_count


def apply_results(rows: list[dict], positions: dict[tuple[str, str, int], dict], results: dict) -> None:
    for key, (status, pnl) in results.items():
        for index in positions[key]["indices"]:
            rows[index]["status"] = status
            rows[index]["resolved_pnl"] = f"{pnl:+.4f}"


def write_rows(csv_file: Path, rows: list[dict], fieldnames: list[str]) -> Path:
    backup = csv_file.with_name("paper_trades_pre_force_resolve.csv")
    shutil.copy2(csv_file, backup)
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return backup


def main():
    rows, fieldnames = load_rows(CSV_FILE)
    positions = build_pending_positions(rows)
    results, resolved_count = resolve_positions(positions)
    skipped_count = len(positions) - resolved_count
    apply_results(rows, positions, results)
    backup = write_rows(CSV_FILE, rows, fieldnames)

    print(f"\nBackup written: {backup}")
    print(f"\n{'=' * 60}")
    print(f"  Resolved : {resolved_count} positions")
    print(f"  Skipped  : {skipped_count} (still live or API error)")
    print(f"  CSV      : {CSV_FILE.resolve()}")
    print(f"{'=' * 60}")
    print("\nNext: sudo systemctl restart polymarket-bot")


if __name__ == "__main__":
    main()
