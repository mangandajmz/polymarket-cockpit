#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from state_store import StateStore


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path(os.getenv("STATE_DB_PATH", str(ROOT / "bot_state.db")))


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _set_default(store: StateStore, key: str, value):
    if store.get_value(key, None) is None:
        store.set_value(key, value)


def initialize_state(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create the local state DB and seed non-destructive default health values."""
    db_path = Path(db_path)
    store = StateStore(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO daily_risk (day_utc, gross_wins, gross_losses)
            VALUES (?, 0.0, 0.0)
            """,
            (_utc_today(),),
        )

    _set_default(store, "initialized_at_utc", _utc_now_str())
    _set_default(store, "budget_day_utc", _utc_today())
    _set_default(store, "closed_pnl", 0.0)
    _set_default(store, "wins", 0)
    _set_default(store, "losses", 0)
    _set_default(store, "daily_losses_per_trader", {})
    _set_default(store, "daily_deploy_per_trader", {})
    _set_default(store, "milestones_reached", [])
    _set_default(store, "whale_sizes", [])
    _set_default(store, "invariant_issues", [])
    _set_default(
        store,
        "health",
        {
            "status_msg": "Initialized; paper bot has not started polling yet.",
            "last_poll": "Never",
            "api_fail_count": 0,
            "last_heartbeat_utc": "",
            "build_version": "unknown",
        },
    )

    return db_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the local paper-trading state database without starting the bot."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Path to bot_state.db (default: STATE_DB_PATH or ./bot_state.db)",
    )
    args = parser.parse_args()

    db_path = initialize_state(args.db)
    print(f"Initialized state DB: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
