from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from worldcup_edge import (
    DEFAULT_PROBABILITY_PATH,
    EdgeRow,
    ProbabilityInput,
    build_edge_board,
    load_probability_file,
)
from worldcup_snapshot import DEFAULT_DB_PATH, WorldCupSnapshotStore, utc_now_str


VALID_STATUSES = {"WATCH", "RECOMMEND", "AVOID"}
RESOLUTION_RESULTS = {"WON": 1.0, "LOST": 0.0, "VOID": None}


@dataclass(frozen=True)
class RecommendationRecord:
    recommendation_id: str
    created_at_utc: str
    status: str
    token_id: str
    question: str
    outcome: str
    user_probability: float
    midpoint: float
    edge: float
    spread: float | None
    best_bid: float | None
    best_ask: float | None
    thesis: str
    note: str
    captured_at_utc: str


class WorldCupRecommendationStore:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS worldcup_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    created_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    user_probability REAL NOT NULL,
                    midpoint REAL NOT NULL,
                    edge REAL NOT NULL,
                    spread REAL,
                    best_bid REAL,
                    best_ask REAL,
                    thesis TEXT NOT NULL,
                    note TEXT NOT NULL,
                    captured_at_utc TEXT NOT NULL,
                    resolved_at_utc TEXT,
                    resolution_result TEXT,
                    resolved_probability REAL,
                    brier_score REAL,
                    market_brier_score REAL,
                    brier_edge REAL,
                    resolution_note TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_worldcup_recommendations_created
                    ON worldcup_recommendations(created_at_utc);
                CREATE INDEX IF NOT EXISTS idx_worldcup_recommendations_token
                    ON worldcup_recommendations(token_id);
                """
            )
            self._ensure_resolution_columns(conn)

    def _ensure_resolution_columns(self, conn: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(worldcup_recommendations)").fetchall()
        }
        columns = {
            "resolved_at_utc": "TEXT",
            "resolution_result": "TEXT",
            "resolved_probability": "REAL",
            "brier_score": "REAL",
            "market_brier_score": "REAL",
            "brier_edge": "REAL",
            "resolution_note": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE worldcup_recommendations ADD COLUMN {name} {ddl}")

    def save_recommendation(self, record: RecommendationRecord) -> RecommendationRecord:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO worldcup_recommendations (
                    recommendation_id, created_at_utc, status, token_id, question,
                    outcome, user_probability, midpoint, edge, spread, best_bid,
                    best_ask, thesis, note, captured_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recommendation_id) DO UPDATE SET
                    created_at_utc=excluded.created_at_utc,
                    status=excluded.status,
                    token_id=excluded.token_id,
                    question=excluded.question,
                    outcome=excluded.outcome,
                    user_probability=excluded.user_probability,
                    midpoint=excluded.midpoint,
                    edge=excluded.edge,
                    spread=excluded.spread,
                    best_bid=excluded.best_bid,
                    best_ask=excluded.best_ask,
                    thesis=excluded.thesis,
                    note=excluded.note,
                    captured_at_utc=excluded.captured_at_utc
                """,
                (
                    record.recommendation_id,
                    record.created_at_utc,
                    record.status,
                    record.token_id,
                    record.question,
                    record.outcome,
                    record.user_probability,
                    record.midpoint,
                    record.edge,
                    record.spread,
                    record.best_bid,
                    record.best_ask,
                    record.thesis,
                    record.note,
                    record.captured_at_utc,
                ),
            )
        return record

    def resolve_recommendation(
        self,
        recommendation_id: str,
        *,
        result: str,
        resolved_at_utc: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        result = result.upper()
        if result not in RESOLUTION_RESULTS:
            raise ValueError(f"result must be one of {', '.join(sorted(RESOLUTION_RESULTS))}")

        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT recommendation_id, user_probability, midpoint
                FROM worldcup_recommendations
                WHERE recommendation_id = ?
                """,
                (recommendation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"recommendation_id {recommendation_id!r} was not found")

            resolved_probability = RESOLUTION_RESULTS[result]
            brier_score = None
            market_brier_score = None
            brier_edge = None
            if resolved_probability is not None:
                user_probability = float(row["user_probability"])
                midpoint = float(row["midpoint"])
                brier_score = round((user_probability - resolved_probability) ** 2, 6)
                market_brier_score = round((midpoint - resolved_probability) ** 2, 6)
                brier_edge = round(market_brier_score - brier_score, 6)

            conn.execute(
                """
                UPDATE worldcup_recommendations
                SET resolved_at_utc = ?,
                    resolution_result = ?,
                    resolved_probability = ?,
                    brier_score = ?,
                    market_brier_score = ?,
                    brier_edge = ?,
                    resolution_note = ?
                WHERE recommendation_id = ?
                """,
                (
                    resolved_at_utc or utc_now_str(),
                    result,
                    resolved_probability,
                    brier_score,
                    market_brier_score,
                    brier_edge,
                    note.strip(),
                    recommendation_id,
                ),
            )
            updated = self._load_recommendation(conn, recommendation_id)
        return updated

    def _load_recommendation(
        self, conn: sqlite3.Connection, recommendation_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            f"""
            SELECT {', '.join(_recommendation_columns())}
            FROM worldcup_recommendations
            WHERE recommendation_id = ?
            """,
            (recommendation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"recommendation_id {recommendation_id!r} was not found")
        return dict(row)

    def load_recommendations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_recommendation_columns())}
                FROM worldcup_recommendations
                ORDER BY created_at_utc DESC, recommendation_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_evaluation_summary(self) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS resolved_count,
                    AVG(brier_score) AS average_brier_score,
                    AVG(market_brier_score) AS average_market_brier_score,
                    AVG(brier_edge) AS average_brier_edge
                FROM worldcup_recommendations
                WHERE resolution_result IN ('WON', 'LOST')
                    AND brier_score IS NOT NULL
                """
            ).fetchone()
        return dict(row)


def _recommendation_columns() -> list[str]:
    return [
        "recommendation_id",
        "created_at_utc",
        "status",
        "token_id",
        "question",
        "outcome",
        "user_probability",
        "midpoint",
        "edge",
        "spread",
        "best_bid",
        "best_ask",
        "thesis",
        "note",
        "captured_at_utc",
        "resolved_at_utc",
        "resolution_result",
        "resolved_probability",
        "brier_score",
        "market_brier_score",
        "brier_edge",
        "resolution_note",
    ]


def make_recommendation_record(
    row: EdgeRow,
    *,
    thesis: str,
    status: str = "WATCH",
    recommendation_id: str | None = None,
    created_at_utc: str | None = None,
) -> RecommendationRecord:
    status = status.upper()
    thesis = thesis.strip()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {', '.join(sorted(VALID_STATUSES))}")
    if not thesis:
        raise ValueError("thesis is required for a paper recommendation")

    return RecommendationRecord(
        recommendation_id=recommendation_id or f"wc-paper-{uuid.uuid4().hex}",
        created_at_utc=created_at_utc or utc_now_str(),
        status=status,
        token_id=row.token_id,
        question=row.question,
        outcome=row.outcome,
        user_probability=row.user_probability,
        midpoint=row.midpoint,
        edge=row.edge,
        spread=row.spread,
        best_bid=row.best_bid,
        best_ask=row.best_ask,
        thesis=thesis,
        note=row.note,
        captured_at_utc=row.captured_at_utc,
    )


def recommend_from_edge(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    probabilities: Mapping[str, ProbabilityInput | float | int],
    token_id: str,
    thesis: str,
    status: str = "WATCH",
    max_spread: float | None = None,
    min_edge: float | None = None,
    recommendation_id: str | None = None,
    created_at_utc: str | None = None,
) -> RecommendationRecord:
    store = WorldCupSnapshotStore(db_path)
    edge_rows = build_edge_board(
        store,
        probabilities,
        max_spread=max_spread,
        min_edge=min_edge,
        limit=10_000,
    )
    matching_row = next((row for row in edge_rows if row.token_id == token_id), None)
    if matching_row is None:
        raise ValueError(f"token_id {token_id!r} was not found in the current edge board")

    record = make_recommendation_record(
        matching_row,
        thesis=thesis,
        status=status,
        recommendation_id=recommendation_id,
        created_at_utc=created_at_utc,
    )
    return WorldCupRecommendationStore(db_path).save_recommendation(record)


def _fmt_price(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.3f}"


def _fmt_edge(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.3f}"


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."


def format_recommendations_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No World Cup paper recommendations have been saved."

    headers = [
        "Created UTC",
        "Status",
        "Question",
        "Outcome",
        "User P",
        "Mid",
        "Edge",
        "Result",
        "Brier",
        "Thesis",
    ]
    widths = [19, 9, 34, 7, 7, 7, 7, 7, 7, 40]
    lines = [
        " | ".join(header.ljust(width) for header, width in zip(headers, widths)),
        "-+-".join("-" * width for width in widths),
    ]
    for row in rows:
        values = [
            str(row.get("created_at_utc") or "").ljust(widths[0]),
            str(row.get("status") or "").ljust(widths[1]),
            _clip(str(row.get("question") or ""), widths[2]).ljust(widths[2]),
            _clip(str(row.get("outcome") or ""), widths[3]).ljust(widths[3]),
            _fmt_price(row.get("user_probability")).rjust(widths[4]),
            _fmt_price(row.get("midpoint")).rjust(widths[5]),
            _fmt_edge(row.get("edge")).rjust(widths[6]),
            str(row.get("resolution_result") or "-").ljust(widths[7]),
            _fmt_price(row.get("brier_score")).rjust(widths[8]),
            _clip(str(row.get("thesis") or ""), widths[9]).ljust(widths[9]),
        ]
        lines.append(" | ".join(values))
    return "\n".join(lines)


def format_evaluation_summary(summary: dict[str, Any]) -> str:
    if not summary.get("resolved_count"):
        return "No resolved World Cup paper recommendations yet."
    return (
        f"Resolved: {summary['resolved_count']} | "
        f"Avg Brier: {_fmt_price(summary.get('average_brier_score'))} | "
        f"Avg Market Brier: {_fmt_price(summary.get('average_market_brier_score'))} | "
        f"Avg Brier Edge: {_fmt_edge(summary.get('average_brier_edge'))}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save, resolve, and list paper-only World Cup recommendations."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--probabilities", default=str(DEFAULT_PROBABILITY_PATH))
    parser.add_argument("--token-id")
    parser.add_argument("--thesis")
    parser.add_argument("--status", choices=sorted(VALID_STATUSES), default="WATCH")
    parser.add_argument("--max-spread", type=float, default=None)
    parser.add_argument("--min-edge", type=float, default=None)
    parser.add_argument("--resolve")
    parser.add_argument("--result", choices=sorted(RESOLUTION_RESULTS))
    parser.add_argument("--resolved-at")
    parser.add_argument("--resolution-note", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    recommendation_store = WorldCupRecommendationStore(args.db)
    if args.summary:
        print(format_evaluation_summary(recommendation_store.load_evaluation_summary()))
        return

    if args.list:
        print(format_recommendations_table(recommendation_store.load_recommendations(limit=args.limit)))
        return

    if args.resolve:
        if not args.result:
            parser.error("--result is required with --resolve")
        record = recommendation_store.resolve_recommendation(
            args.resolve,
            result=args.result,
            resolved_at_utc=args.resolved_at,
            note=args.resolution_note,
        )
        print(f"Resolved World Cup paper recommendation {record['recommendation_id']}.")
        print()
        print(format_recommendations_table([record]))
        print()
        print(format_evaluation_summary(recommendation_store.load_evaluation_summary()))
        return

    if not args.token_id or not args.thesis:
        parser.error("--token-id and --thesis are required unless --list, --summary, or --resolve is used")

    probabilities = load_probability_file(args.probabilities)
    record = recommend_from_edge(
        db_path=args.db,
        probabilities=probabilities,
        token_id=args.token_id,
        thesis=args.thesis,
        status=args.status,
        max_spread=args.max_spread,
        min_edge=args.min_edge,
    )
    print(f"Saved World Cup paper recommendation {record.recommendation_id}.")
    print()
    print(format_recommendations_table([asdict(record)]))


if __name__ == "__main__":
    main()
