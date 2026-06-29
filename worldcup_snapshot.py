from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid
from typing import Any

from worldcup_api_spike import PolymarketWorldCupSpike, WorldCupSpikeResult


DEFAULT_DB_PATH = Path("worldcup_markets.db")


class ReadOnlyBoundaryError(RuntimeError):
    pass


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _rfq_support_label(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "true" if value else "false"


class WorldCupSnapshotStore:
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

                CREATE TABLE IF NOT EXISTS worldcup_snapshot_runs (
                    snapshot_id TEXT PRIMARY KEY,
                    captured_at_utc TEXT NOT NULL,
                    query TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    market_count INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    sample_book_count INTEGER NOT NULL,
                    combo_candidate_count INTEGER NOT NULL,
                    wallet_auth_used INTEGER NOT NULL,
                    unsigned_rfq_quote_supported TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worldcup_events (
                    event_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    last_seen_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worldcup_markets (
                    market_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    active INTEGER,
                    closed INTEGER,
                    enable_order_book INTEGER,
                    is_combo_candidate INTEGER NOT NULL,
                    last_seen_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worldcup_market_tokens (
                    token_id TEXT PRIMARY KEY,
                    market_id TEXT NOT NULL,
                    outcome_index INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    last_seen_at_utc TEXT NOT NULL,
                    FOREIGN KEY (market_id) REFERENCES worldcup_markets(market_id)
                );

                CREATE TABLE IF NOT EXISTS worldcup_order_book_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    captured_at_utc TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    best_bid REAL,
                    best_ask REAL,
                    midpoint REAL,
                    FOREIGN KEY (snapshot_id) REFERENCES worldcup_snapshot_runs(snapshot_id),
                    FOREIGN KEY (token_id) REFERENCES worldcup_market_tokens(token_id)
                );

                CREATE INDEX IF NOT EXISTS idx_worldcup_books_token_time
                    ON worldcup_order_book_snapshots(token_id, captured_at_utc);
                CREATE INDEX IF NOT EXISTS idx_worldcup_tokens_market
                    ON worldcup_market_tokens(market_id);
                """
            )

    def save_result(
        self,
        result: WorldCupSpikeResult,
        *,
        captured_at_utc: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        if result.wallet_auth_used:
            raise ReadOnlyBoundaryError(
                "World Cup snapshots must come from read-only public API results."
            )

        captured_at_utc = captured_at_utc or utc_now_str()
        snapshot_id = snapshot_id or f"wc-{uuid.uuid4().hex}"
        token_count = sum(len(market.clob_token_ids) for market in result.markets)
        book_by_token = {book.token_id: book for book in result.sample_books}

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO worldcup_snapshot_runs (
                    snapshot_id, captured_at_utc, query, event_count, market_count,
                    token_count, sample_book_count, combo_candidate_count,
                    wallet_auth_used, unsigned_rfq_quote_supported
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    captured_at_utc,
                    result.query,
                    len(result.events),
                    len(result.markets),
                    token_count,
                    len(result.sample_books),
                    result.combo_candidate_count,
                    0,
                    _rfq_support_label(result.unsigned_rfq_quote_supported),
                ),
            )

            for event in result.events:
                conn.execute(
                    """
                    INSERT INTO worldcup_events (
                        event_id, title, slug, last_seen_at_utc
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        title=excluded.title,
                        slug=excluded.slug,
                        last_seen_at_utc=excluded.last_seen_at_utc
                    """,
                    (event.id, event.title, event.slug, captured_at_utc),
                )

            for market in result.markets:
                conn.execute(
                    """
                    INSERT INTO worldcup_markets (
                        market_id, question, condition_id, active, closed,
                        enable_order_book, is_combo_candidate, last_seen_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market_id) DO UPDATE SET
                        question=excluded.question,
                        condition_id=excluded.condition_id,
                        active=excluded.active,
                        closed=excluded.closed,
                        enable_order_book=excluded.enable_order_book,
                        is_combo_candidate=excluded.is_combo_candidate,
                        last_seen_at_utc=excluded.last_seen_at_utc
                    """,
                    (
                        market.id,
                        market.question,
                        market.condition_id,
                        _bool_to_int(market.active),
                        _bool_to_int(market.closed),
                        _bool_to_int(market.enable_order_book),
                        1 if market.is_combo_candidate else 0,
                        captured_at_utc,
                    ),
                )

                for idx, token_id in enumerate(market.clob_token_ids):
                    outcome = market.outcomes[idx] if idx < len(market.outcomes) else ""
                    conn.execute(
                        """
                        INSERT INTO worldcup_market_tokens (
                            token_id, market_id, outcome_index, outcome, last_seen_at_utc
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(token_id) DO UPDATE SET
                            market_id=excluded.market_id,
                            outcome_index=excluded.outcome_index,
                            outcome=excluded.outcome,
                            last_seen_at_utc=excluded.last_seen_at_utc
                        """,
                        (token_id, market.id, idx, outcome, captured_at_utc),
                    )

                    book = book_by_token.get(token_id)
                    if book is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO worldcup_order_book_snapshots (
                            snapshot_id, captured_at_utc, token_id,
                            best_bid, best_ask, midpoint
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            captured_at_utc,
                            token_id,
                            book.best_bid,
                            book.best_ask,
                            book.midpoint,
                        ),
                    )

        return {
            "snapshot_id": snapshot_id,
            "captured_at_utc": captured_at_utc,
            "event_count": len(result.events),
            "market_count": len(result.markets),
            "token_count": token_count,
            "book_snapshot_count": len(result.sample_books),
            "combo_candidate_count": result.combo_candidate_count,
        }

    def load_latest_odds(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                WITH latest_books AS (
                    SELECT token_id, MAX(id) AS latest_id
                    FROM worldcup_order_book_snapshots
                    GROUP BY token_id
                )
                SELECT
                    m.question,
                    t.outcome,
                    t.token_id,
                    b.best_bid,
                    b.best_ask,
                    b.midpoint,
                    CASE
                        WHEN b.best_bid IS NOT NULL AND b.best_ask IS NOT NULL
                        THEN ROUND(b.best_ask - b.best_bid, 6)
                        ELSE NULL
                    END AS spread,
                    b.captured_at_utc
                FROM latest_books lb
                JOIN worldcup_order_book_snapshots b ON b.id = lb.latest_id
                JOIN worldcup_market_tokens t ON t.token_id = b.token_id
                JOIN worldcup_markets m ON m.market_id = t.market_id
                ORDER BY b.captured_at_utc DESC, m.question, t.outcome
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _fmt_price(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.3f}"


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."


def format_odds_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No sampled World Cup order books found."

    headers = ["Question", "Outcome", "Bid", "Ask", "Mid", "Spread", "Updated UTC"]
    widths = [44, 10, 7, 7, 7, 8, 19]
    lines = [
        " | ".join(header.ljust(width) for header, width in zip(headers, widths)),
        "-+-".join("-" * width for width in widths),
    ]
    for row in rows:
        values = [
            _clip(str(row.get("question") or ""), widths[0]).ljust(widths[0]),
            _clip(str(row.get("outcome") or ""), widths[1]).ljust(widths[1]),
            _fmt_price(row.get("best_bid")).rjust(widths[2]),
            _fmt_price(row.get("best_ask")).rjust(widths[3]),
            _fmt_price(row.get("midpoint")).rjust(widths[4]),
            _fmt_price(row.get("spread")).rjust(widths[5]),
            str(row.get("captured_at_utc") or "").ljust(widths[6]),
        ]
        lines.append(" | ".join(values))
    return "\n".join(lines)


def run_snapshot(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    query: str = "2026 FIFA World Cup",
    limit: int = 100,
    sample_price_count: int = 25,
    odds_limit: int = 25,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spike = PolymarketWorldCupSpike(sample_price_count=sample_price_count)
    result = spike.run(query=query, limit=limit)
    store = WorldCupSnapshotStore(db_path)
    summary = store.save_result(result)
    return summary, store.load_latest_odds(limit=odds_limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persist a read-only Polymarket World Cup market snapshot."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--query", default="2026 FIFA World Cup")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sample-price-count", type=int, default=25)
    parser.add_argument("--odds-limit", type=int, default=25)
    args = parser.parse_args()

    summary, odds_rows = run_snapshot(
        db_path=args.db,
        query=args.query,
        limit=args.limit,
        sample_price_count=args.sample_price_count,
        odds_limit=args.odds_limit,
    )
    print(
        "Saved World Cup snapshot "
        f"{summary['snapshot_id']} at {summary['captured_at_utc']} "
        f"({summary['market_count']} markets, {summary['token_count']} tokens, "
        f"{summary['book_snapshot_count']} sampled books)."
    )
    print()
    print(format_odds_table(odds_rows))


if __name__ == "__main__":
    main()
