#!/usr/bin/env python3
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _parse_json(value: str):
    try:
        return json.loads(value)
    except Exception:
        return value


def _load_kv(conn) -> dict:
    rows = conn.execute("SELECT key, value FROM kv_state").fetchall()
    return {key: _parse_json(value) for key, value in rows}


def _fmt_money(value) -> str:
    try:
        return f"${float(value):+,.2f}"
    except Exception:
        return "n/a"


def _fmt_ts_age(ts_str: str) -> str:
    if not ts_str:
        return "unknown"
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        return f"{ts_str} UTC ({age_s:.0f}s ago)"
    except Exception:
        return ts_str


def main():
    parser = argparse.ArgumentParser(description="Inspect bot health from the canonical state DB.")
    parser.add_argument(
        "--db",
        default=os.getenv("STATE_DB_PATH", str(Path(__file__).parent / "bot_state.db")),
        help="Path to bot_state.db",
    )
    parser.add_argument(
        "--positions",
        type=int,
        default=5,
        help="Number of open positions to show",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"State DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        kv = _load_kv(conn)
        today = datetime.now(timezone.utc).date().isoformat()
        daily = conn.execute(
            "SELECT gross_wins, gross_losses FROM daily_risk WHERE day_utc = ?",
            (today,),
        ).fetchone()
        positions = conn.execute(
            """
            SELECT trader, market, outcome, total_cost, total_shares, last_price, pnl, opened_at_utc
            FROM positions
            WHERE status = 'OPEN'
            ORDER BY total_cost DESC
            LIMIT ?
            """,
            (args.positions,),
        ).fetchall()
        trader_stats = conn.execute(
            """
            SELECT trader, wins, losses
            FROM trader_stats
            ORDER BY (wins + losses) DESC, trader ASC
            """
        ).fetchall()
        open_count = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status = 'OPEN'"
        ).fetchone()["n"]
        closed_count = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status IN ('WIN', 'LOSS')"
        ).fetchone()["n"]
    finally:
        conn.close()

    health = kv.get("health", {}) if isinstance(kv.get("health"), dict) else {}
    watchlist = kv.get("watchlist_health", {}) if isinstance(kv.get("watchlist_health"), dict) else {}
    invariant_issues = kv.get("invariant_issues", []) or []

    print("Polymarket Bot Health")
    print("=====================")
    print(f"DB: {db_path}")
    print(f"Bot build: {health.get('build_version', 'unknown')}")
    print(f"Last heartbeat: {_fmt_ts_age(health.get('last_heartbeat_utc', ''))}")
    print(f"Last poll: {health.get('last_poll', 'unknown')}")
    print(f"API failures: {health.get('api_fail_count', 'unknown')}")
    print(f"Status: {health.get('status_msg', 'unknown')}")
    print()

    gross_wins = float(daily["gross_wins"]) if daily else 0.0
    gross_losses = float(daily["gross_losses"]) if daily else 0.0
    print("Risk Today")
    print("---------")
    print(f"Gross wins:   {_fmt_money(gross_wins)}")
    print(f"Gross losses: {_fmt_money(gross_losses)}")
    print(f"Net loss:     {_fmt_money(gross_losses - gross_wins)}")
    print(f"Open positions: {open_count}")
    print(f"Closed positions: {closed_count}")
    print()

    print("Watchlist")
    print("---------")
    print(f"Active count: {watchlist.get('active_count', 'unknown')}")
    print(f"Last refresh: {watchlist.get('last_successful_refresh', 'unknown')}")
    if watchlist.get("last_error"):
        print(f"Last error:   {watchlist['last_error']}")
    active_names = watchlist.get("active_names", []) or []
    if active_names:
        print(f"Active names: {', '.join(active_names)}")
    print()

    print("Top Open Positions")
    print("------------------")
    if not positions:
        print("No open positions.")
    else:
        for row in positions:
            print(
                f"{row['trader']}: {row['market'][:50]} | {row['outcome']} | "
                f"cost {_fmt_money(row['total_cost'])} | pnl {_fmt_money(row['pnl'])} | "
                f"last_px={row['last_price']} | opened={row['opened_at_utc']}"
            )
    print()

    print("Trader Stats")
    print("------------")
    if not trader_stats:
        print("No trader stats yet.")
    else:
        for row in trader_stats[:10]:
            total = row["wins"] + row["losses"]
            wr = (row["wins"] / total * 100.0) if total else 0.0
            print(f"{row['trader']}: {row['wins']}W/{row['losses']}L ({wr:.1f}%, {total} total)")
    print()

    print("Invariant Issues")
    print("----------------")
    if not invariant_issues:
        print("None")
    else:
        for issue in invariant_issues[:10]:
            print(f"- {issue}")


if __name__ == "__main__":
    main()
