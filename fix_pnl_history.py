"""
fix_pnl_history.py
==================
One-off migration script to correct stale resolved_pnl values in
paper_trades.csv written by an older version of the bot that stored
the whale's total market payout instead of our proportional profit.

Correct formulas:
  WIN  → corrected_pnl = our_cost * (1 / entry_price - 1)
           = (our_cost / entry_price) - our_cost   (proceeds minus cost)
  LOSS → corrected_pnl = -our_cost

Detection threshold for suspect WIN rows:
  resolved_pnl > our_cost * 5
  (a legitimate maximum-return win at entry price $0.10 returns ~9x cost,
   so 5x is a conservative floor that catches whale payouts without
   touching any legitimate high-return win)

Detection for suspect LOSS rows:
  abs(resolved_pnl - (-our_cost)) > 0.01

Usage:
  python fix_pnl_history.py [--csv path/to/paper_trades.csv]
"""

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd


# ── Column names (must match paper_trading_bot.py CSV_FIELDS) ─────────────────
COL_STATUS      = "status"
COL_PNL         = "resolved_pnl"
COL_COST        = "our_size_usdc"
COL_PRICE       = "price"          # entry price at time of trade
WIN_SUSPECT_MULT = 5.0             # flag WIN rows where pnl > cost × this

# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str)
    # Verify required columns exist
    missing = [c for c in (COL_STATUS, COL_PNL, COL_COST, COL_PRICE) if c not in df.columns]
    if missing:
        sys.exit(f"[ERROR] CSV is missing expected columns: {missing}\n"
                 f"        Columns found: {list(df.columns)}")
    return df


def _identify_suspects(df: pd.DataFrame):
    cost  = _to_float(df[COL_COST])
    pnl   = _to_float(df[COL_PNL])
    price = _to_float(df[COL_PRICE])

    is_win  = df[COL_STATUS] == "WIN"
    is_loss = df[COL_STATUS] == "LOSS"

    # WIN rows where pnl is implausibly large (> 5× our stake)
    suspect_win_mask = is_win & (pnl > cost * WIN_SUSPECT_MULT)

    # LOSS rows where resolved_pnl deviates from -our_cost by more than $0.01
    expected_loss_pnl = -cost
    suspect_loss_mask = is_loss & (abs(pnl - expected_loss_pnl) > 0.01)

    return suspect_win_mask, suspect_loss_mask, cost, pnl, price


def _correct(df: pd.DataFrame,
             suspect_win_mask: pd.Series,
             suspect_loss_mask: pd.Series,
             cost: pd.Series,
             price: pd.Series) -> pd.DataFrame:
    df = df.copy()

    # WIN correction: pnl = our_cost * (1 / entry_price - 1)
    # Guard against zero/negative entry prices — skip those rows with a warning.
    bad_price = suspect_win_mask & (price <= 0)
    if bad_price.any():
        print(f"  [WARN] {bad_price.sum()} WIN row(s) have entry_price ≤ 0 "
              f"and cannot be corrected — skipped.")
    correctable_wins = suspect_win_mask & (price > 0)
    corrected_win_pnl = cost[correctable_wins] * (1.0 / price[correctable_wins] - 1.0)
    df.loc[correctable_wins, COL_PNL] = corrected_win_pnl.map("{:+.4f}".format)

    # LOSS correction: pnl = -our_cost exactly
    df.loc[suspect_loss_mask, COL_PNL] = (-cost[suspect_loss_mask]).map("{:+.4f}".format)

    return df


def _print_suspect_preview(df: pd.DataFrame,
                            win_mask: pd.Series,
                            loss_mask: pd.Series,
                            cost: pd.Series,
                            pnl: pd.Series):
    combined = win_mask | loss_mask
    n_win  = int(win_mask.sum())
    n_loss = int(loss_mask.sum())
    n_total = n_win + n_loss

    print(f"\n{'─' * 66}")
    print(f"  Total rows in file      : {len(df)}")
    print(f"  Suspect WIN  rows       : {n_win}  "
          f"(resolved_pnl > {WIN_SUSPECT_MULT:.0f}× our_size_usdc)")
    print(f"  Suspect LOSS rows       : {n_loss}  "
          f"(resolved_pnl ≠ -our_size_usdc within $0.01)")
    print(f"  Total suspect rows      : {n_total}")
    if n_total == 0:
        return

    suspect_pnl_sum = pnl[combined].sum()
    print(f"  Sum of suspect pnl      : ${suspect_pnl_sum:+,.4f}  (before correction)")
    print(f"{'─' * 66}")

    preview = df[combined].copy()
    preview["_cost"]  = cost[combined].values
    preview["_pnl"]   = pnl[combined].values
    preview["_flag"]  = "WIN?" * win_mask[combined].astype(int).values + \
                        "LOSS?" * loss_mask[combined].astype(int).values

    display_cols = ["timestamp", "trader", "market", "outcome",
                    COL_COST, COL_PRICE, COL_STATUS, COL_PNL]
    display_cols = [c for c in display_cols if c in preview.columns]

    print(f"\n  First {min(10, n_total)} suspect row(s):\n")
    pd.set_option("display.max_colwidth", 28)
    pd.set_option("display.width", 132)
    print(preview[display_cols].head(10).to_string(index=True))
    print()


def main():
    parser = argparse.ArgumentParser(description="Correct stale resolved_pnl rows in paper_trades.csv")
    parser.add_argument(
        "--csv",
        default="paper_trades.csv",
        help="Path to paper_trades.csv (default: paper_trades.csv in current directory)",
    )
    args = parser.parse_args()

    csv_path    = Path(args.csv)
    backup_path = csv_path.with_name("paper_trades_backup.csv")

    if not csv_path.exists():
        sys.exit(f"[ERROR] File not found: {csv_path.resolve()}")

    print(f"\n  Reading: {csv_path.resolve()}")
    df = _load(csv_path)

    win_mask, loss_mask, cost, pnl, price = _identify_suspects(df)
    combined = win_mask | loss_mask
    n_suspect = int(combined.sum())

    _print_suspect_preview(df, win_mask, loss_mask, cost, pnl)

    if n_suspect == 0:
        print("  No suspect rows found — nothing to correct.")
        sys.exit(0)

    # ── User confirmation ─────────────────────────────────────────────────────
    try:
        answer = input(f"  Correct these {n_suspect} row(s)? (yes/no): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Aborted.")
        sys.exit(0)

    if answer != "yes":
        print("  Aborted — no changes written.")
        sys.exit(0)

    # ── Backup ────────────────────────────────────────────────────────────────
    shutil.copy2(csv_path, backup_path)
    print(f"\n  Backup written : {backup_path.resolve()}")

    # ── Correction ────────────────────────────────────────────────────────────
    pnl_before = _to_float(df[COL_PNL])
    total_pnl_before = float(pnl_before[combined].sum())

    df_corrected = _correct(df, win_mask, loss_mask, cost, price)

    pnl_after = _to_float(df_corrected[COL_PNL])
    total_pnl_after = float(pnl_after[combined].sum())

    # ── Write ─────────────────────────────────────────────────────────────────
    df_corrected.to_csv(csv_path, index=False)
    print(f"  Written        : {csv_path.resolve()}")

    # ── Final summary ─────────────────────────────────────────────────────────
    n_win_corrected  = int((win_mask  & (price > 0)).sum())
    n_loss_corrected = int(loss_mask.sum())

    print(f"\n{'─' * 66}")
    print(f"  Rows corrected          : {n_win_corrected + n_loss_corrected}"
          f"  ({n_win_corrected} WIN, {n_loss_corrected} LOSS)")
    print(f"  Sum pnl before          : ${total_pnl_before:+,.4f}")
    print(f"  Sum pnl after           : ${total_pnl_after:+,.4f}")
    print(f"  Net difference          : ${total_pnl_after - total_pnl_before:+,.4f}")
    print(f"{'─' * 66}\n")


if __name__ == "__main__":
    main()
