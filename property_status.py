from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATUS_PATH = STATE_DIR / "status.json"
DEFAULT_DB_PATH = Path(os.getenv("STATE_DB_PATH", str(ROOT / "bot_state.db")))

PROPERTY_ID = "polymarket_cockpit"
DISPLAY_NAME = "Polymarket Recommendation Cockpit"
SCHEMA_VERSION = "property_status.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _status_from_db(db_path: Path) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    if not db_path.exists():
        return (
            "RED",
            "Polymarket cockpit migrated, but no local paper-trading state database exists in the new repo yet.",
            {
                "state_db_present": 0,
                "recommendations_total": 0,
                "open_recommendations": 0,
                "open_positions": 0,
                "unresolved_opportunities": 0,
            },
            {
                "brief": "Run the bot or restore intentional local state before relying on recommendations.",
                "severity": "RED",
                "evidence_ref": "README.md",
            },
        )

    with sqlite3.connect(db_path) as conn:
        recommendations_total = 0
        open_recommendations = 0
        avoid_recommendations = 0
        if _table_exists(conn, "recommendations"):
            recommendations_total = _count(conn, "SELECT COUNT(*) FROM recommendations")
            open_recommendations = _count(
                conn,
                """
                SELECT COUNT(*) FROM recommendations
                WHERE status IN ('RECOMMEND', 'WATCH')
                  AND resolution_status IS NULL
                """,
            )
            avoid_recommendations = _count(
                conn,
                "SELECT COUNT(*) FROM recommendations WHERE status = 'AVOID'",
            )

        open_positions = 0
        if _table_exists(conn, "positions"):
            open_positions = _count(conn, "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'")

        unresolved_opportunities = 0
        if _table_exists(conn, "opportunities"):
            unresolved_opportunities = _count(
                conn,
                "SELECT COUNT(*) FROM opportunities WHERE resolution_status IS NULL",
            )

    kpis = {
        "state_db_present": 1,
        "recommendations_total": recommendations_total,
        "open_recommendations": open_recommendations,
        "avoid_recommendations": avoid_recommendations,
        "open_positions": open_positions,
        "unresolved_opportunities": unresolved_opportunities,
    }
    headline = (
        f"Paper cockpit ready: {open_recommendations} open recommendations, "
        f"{open_positions} open paper positions."
    )
    return "GREEN", headline, kpis, None


def build_status(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_path = Path(db_path)
    try:
        status, headline, kpis, top_issue = _status_from_db(db_path)
    except sqlite3.Error as exc:
        status = "BLOCKED"
        headline = "Polymarket cockpit cannot read its local state database."
        kpis = {
            "state_db_present": 1 if db_path.exists() else 0,
            "recommendations_total": 0,
            "open_recommendations": 0,
            "open_positions": 0,
            "unresolved_opportunities": 0,
        }
        top_issue = {
            "brief": f"SQLite state read failed: {exc}",
            "severity": "BLOCKED",
            "evidence_ref": str(db_path),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "property_id": PROPERTY_ID,
        "display_name": DISPLAY_NAME,
        "as_of": _utc_now_iso(),
        "status": status,
        "headline": headline[:200],
        "top_issue": top_issue,
        "approvals_pending": [],
        "kpis": kpis,
        "evidence_ref": "README.md",
    }


def write_status(path: Path | str = STATUS_PATH, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    status = build_status(db_path)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    print(json.dumps(write_status(), indent=2, sort_keys=True))
