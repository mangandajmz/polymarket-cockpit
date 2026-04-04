import json
import sqlite3
import threading
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS copied_fills (
                    event_id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    trader TEXT NOT NULL,
                    market TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    whale_side TEXT NOT NULL,
                    whale_size_usdc REAL NOT NULL,
                    our_size_usdc REAL NOT NULL,
                    price REAL NOT NULL,
                    copy_shares REAL NOT NULL,
                    conviction REAL NOT NULL,
                    status TEXT NOT NULL,
                    resolved_pnl REAL,
                    condition_id TEXT NOT NULL,
                    outcome_index INTEGER NOT NULL,
                    transaction_hash TEXT,
                    source_timestamp INTEGER
                );

                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    trader TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    outcome_index INTEGER NOT NULL,
                    market TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    opened_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_cost REAL NOT NULL,
                    total_shares REAL NOT NULL,
                    last_price REAL,
                    pnl REAL NOT NULL,
                    close_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS trader_stats (
                    trader TEXT PRIMARY KEY,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS daily_risk (
                    day_utc TEXT PRIMARY KEY,
                    gross_wins REAL NOT NULL DEFAULT 0,
                    gross_losses REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS kv_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS opportunities (
                    event_id TEXT PRIMARY KEY,
                    observed_at_utc TEXT NOT NULL,
                    trader TEXT NOT NULL,
                    market TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    whale_side TEXT NOT NULL,
                    whale_size_usdc REAL NOT NULL,
                    price REAL NOT NULL,
                    condition_id TEXT NOT NULL,
                    outcome_index INTEGER NOT NULL,
                    transaction_hash TEXT,
                    source_timestamp INTEGER,
                    opportunity_age_sec INTEGER NOT NULL DEFAULT 0,
                    trader_resolved_count INTEGER NOT NULL DEFAULT 0,
                    trader_win_rate REAL,
                    daily_losses_for_trader INTEGER NOT NULL DEFAULT 0,
                    daily_deploy_for_trader REAL NOT NULL DEFAULT 0,
                    bankroll REAL,
                    deployed_cap_pct REAL,
                    open_positions_count INTEGER NOT NULL DEFAULT 0,
                    median_whale_size REAL,
                    conviction REAL,
                    perf_mult REAL,
                    dynamic_max_bet REAL,
                    recommended_size REAL,
                    copied_size_usdc REAL,
                    copy_shares REAL,
                    position_id TEXT,
                    decision TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    is_crypto INTEGER NOT NULL DEFAULT 0,
                    is_spread INTEGER NOT NULL DEFAULT 0,
                    is_futures INTEGER NOT NULL DEFAULT 0,
                    price_capped INTEGER NOT NULL DEFAULT 0,
                    duplicate_game INTEGER NOT NULL DEFAULT 0,
                    base_game TEXT,
                    bayes_posterior_mean REAL,
                    bayes_lower_bound REAL,
                    shadow_model_score REAL,
                    shadow_model_decision TEXT,
                    resolution_status TEXT,
                    resolved_pnl REAL,
                    resolved_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_fills_position_id
                    ON copied_fills(position_id);
                CREATE INDEX IF NOT EXISTS idx_fills_timestamp
                    ON copied_fills(timestamp_utc);
                CREATE INDEX IF NOT EXISTS idx_positions_status
                    ON positions(status);
                CREATE INDEX IF NOT EXISTS idx_opportunities_observed
                    ON opportunities(observed_at_utc);
                CREATE INDEX IF NOT EXISTS idx_opportunities_position_id
                    ON opportunities(position_id);
                """
            )
        self._ensure_opportunity_columns()

    def _ensure_opportunity_columns(self):
        required = {
            "bayes_posterior_mean": "REAL",
            "bayes_lower_bound": "REAL",
            "shadow_model_score": "REAL",
            "shadow_model_decision": "TEXT",
        }
        with self._connect() as conn:
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()
            }
            for name, col_type in required.items():
                if name not in cols:
                    conn.execute(
                        f"ALTER TABLE opportunities ADD COLUMN {name} {col_type}"
                    )

    def load_seen_events(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_id FROM copied_fills").fetchall()
        return {row["event_id"] for row in rows}

    def upsert_fill(self, record: dict):
        payload = {
            "event_id": record.get("event_id", ""),
            "position_id": record.get("position_id", ""),
            "timestamp_utc": record.get("timestamp", ""),
            "trader": record.get("trader", ""),
            "market": record.get("market", ""),
            "outcome": record.get("outcome", ""),
            "whale_side": record.get("whale_side", ""),
            "whale_size_usdc": float(record.get("whale_size_usdc", 0) or 0),
            "our_size_usdc": float(record.get("our_size_usdc", 0) or 0),
            "price": float(record.get("price", 0) or 0),
            "copy_shares": float(record.get("copy_shares", 0) or 0),
            "conviction": float(record.get("conviction", 0) or 0),
            "status": record.get("status", "PENDING"),
            "resolved_pnl": (
                float(record.get("resolved_pnl", 0))
                if str(record.get("resolved_pnl", "")).strip() not in ("", "None")
                else None
            ),
            "condition_id": record.get("condition_id", ""),
            "outcome_index": int(record.get("outcome_index", 0) or 0),
            "transaction_hash": record.get("transaction_hash", ""),
            "source_timestamp": int(record.get("source_timestamp", 0) or 0),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO copied_fills (
                    event_id, position_id, timestamp_utc, trader, market, outcome,
                    whale_side, whale_size_usdc, our_size_usdc, price, copy_shares,
                    conviction, status, resolved_pnl, condition_id, outcome_index,
                    transaction_hash, source_timestamp
                ) VALUES (
                    :event_id, :position_id, :timestamp_utc, :trader, :market, :outcome,
                    :whale_side, :whale_size_usdc, :our_size_usdc, :price, :copy_shares,
                    :conviction, :status, :resolved_pnl, :condition_id, :outcome_index,
                    :transaction_hash, :source_timestamp
                )
                ON CONFLICT(event_id) DO UPDATE SET
                    position_id=excluded.position_id,
                    timestamp_utc=excluded.timestamp_utc,
                    trader=excluded.trader,
                    market=excluded.market,
                    outcome=excluded.outcome,
                    whale_side=excluded.whale_side,
                    whale_size_usdc=excluded.whale_size_usdc,
                    our_size_usdc=excluded.our_size_usdc,
                    price=excluded.price,
                    copy_shares=excluded.copy_shares,
                    conviction=excluded.conviction,
                    status=excluded.status,
                    resolved_pnl=excluded.resolved_pnl,
                    condition_id=excluded.condition_id,
                    outcome_index=excluded.outcome_index,
                    transaction_hash=excluded.transaction_hash,
                    source_timestamp=excluded.source_timestamp
                """,
                payload,
            )

    def update_position(self, pos: dict, updated_at_utc: str, close_reason: str | None = None):
        payload = {
            "position_id": pos["position_id"],
            "trader": pos["trader"],
            "condition_id": pos["condition_id"],
            "outcome_index": int(pos["outcome_index"]),
            "market": pos["title"],
            "outcome": pos["outcome"],
            "opened_at_utc": pos["opened_at_utc"],
            "updated_at_utc": updated_at_utc,
            "status": pos["status"],
            "total_cost": float(pos["total_cost"]),
            "total_shares": float(pos["total_shares"]),
            "last_price": (
                float(pos["last_price"])
                if pos.get("last_price") is not None
                else None
            ),
            "pnl": float(pos["pnl"]),
            "close_reason": close_reason,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    position_id, trader, condition_id, outcome_index, market, outcome,
                    opened_at_utc, updated_at_utc, status, total_cost, total_shares,
                    last_price, pnl, close_reason
                ) VALUES (
                    :position_id, :trader, :condition_id, :outcome_index, :market, :outcome,
                    :opened_at_utc, :updated_at_utc, :status, :total_cost, :total_shares,
                    :last_price, :pnl, :close_reason
                )
                ON CONFLICT(position_id) DO UPDATE SET
                    trader=excluded.trader,
                    condition_id=excluded.condition_id,
                    outcome_index=excluded.outcome_index,
                    market=excluded.market,
                    outcome=excluded.outcome,
                    opened_at_utc=excluded.opened_at_utc,
                    updated_at_utc=excluded.updated_at_utc,
                    status=excluded.status,
                    total_cost=excluded.total_cost,
                    total_shares=excluded.total_shares,
                    last_price=excluded.last_price,
                    pnl=excluded.pnl,
                    close_reason=excluded.close_reason
                """,
                payload,
            )

    def update_fill_resolution(self, position_id: str, status: str, pnl: float):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE copied_fills
                   SET status = ?, resolved_pnl = ?
                 WHERE position_id = ?
                """,
                (status, pnl, position_id),
            )

    def upsert_opportunity(self, record: dict):
        payload = {
            "event_id": record.get("event_id", ""),
            "observed_at_utc": record.get("observed_at_utc", ""),
            "trader": record.get("trader", ""),
            "market": record.get("market", ""),
            "outcome": record.get("outcome", ""),
            "whale_side": record.get("whale_side", ""),
            "whale_size_usdc": float(record.get("whale_size_usdc", 0) or 0),
            "price": float(record.get("price", 0) or 0),
            "condition_id": record.get("condition_id", ""),
            "outcome_index": int(record.get("outcome_index", 0) or 0),
            "transaction_hash": record.get("transaction_hash", ""),
            "source_timestamp": int(record.get("source_timestamp", 0) or 0),
            "opportunity_age_sec": int(record.get("opportunity_age_sec", 0) or 0),
            "trader_resolved_count": int(record.get("trader_resolved_count", 0) or 0),
            "trader_win_rate": (
                float(record.get("trader_win_rate"))
                if record.get("trader_win_rate") is not None
                else None
            ),
            "daily_losses_for_trader": int(record.get("daily_losses_for_trader", 0) or 0),
            "daily_deploy_for_trader": float(record.get("daily_deploy_for_trader", 0) or 0),
            "bankroll": (
                float(record.get("bankroll"))
                if record.get("bankroll") is not None
                else None
            ),
            "deployed_cap_pct": (
                float(record.get("deployed_cap_pct"))
                if record.get("deployed_cap_pct") is not None
                else None
            ),
            "open_positions_count": int(record.get("open_positions_count", 0) or 0),
            "median_whale_size": (
                float(record.get("median_whale_size"))
                if record.get("median_whale_size") is not None
                else None
            ),
            "conviction": (
                float(record.get("conviction"))
                if record.get("conviction") is not None
                else None
            ),
            "perf_mult": (
                float(record.get("perf_mult"))
                if record.get("perf_mult") is not None
                else None
            ),
            "dynamic_max_bet": (
                float(record.get("dynamic_max_bet"))
                if record.get("dynamic_max_bet") is not None
                else None
            ),
            "recommended_size": (
                float(record.get("recommended_size"))
                if record.get("recommended_size") is not None
                else None
            ),
            "copied_size_usdc": (
                float(record.get("copied_size_usdc"))
                if record.get("copied_size_usdc") is not None
                else None
            ),
            "copy_shares": (
                float(record.get("copy_shares"))
                if record.get("copy_shares") is not None
                else None
            ),
            "position_id": record.get("position_id"),
            "decision": record.get("decision", "SEEN"),
            "decision_reason": record.get("decision_reason", ""),
            "is_crypto": 1 if record.get("is_crypto") else 0,
            "is_spread": 1 if record.get("is_spread") else 0,
            "is_futures": 1 if record.get("is_futures") else 0,
            "price_capped": 1 if record.get("price_capped") else 0,
            "duplicate_game": 1 if record.get("duplicate_game") else 0,
            "base_game": record.get("base_game", ""),
            "bayes_posterior_mean": (
                float(record.get("bayes_posterior_mean"))
                if record.get("bayes_posterior_mean") is not None
                else None
            ),
            "bayes_lower_bound": (
                float(record.get("bayes_lower_bound"))
                if record.get("bayes_lower_bound") is not None
                else None
            ),
            "shadow_model_score": (
                float(record.get("shadow_model_score"))
                if record.get("shadow_model_score") is not None
                else None
            ),
            "shadow_model_decision": record.get("shadow_model_decision"),
            "resolution_status": record.get("resolution_status"),
            "resolved_pnl": (
                float(record.get("resolved_pnl"))
                if record.get("resolved_pnl") is not None
                else None
            ),
            "resolved_at_utc": record.get("resolved_at_utc"),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO opportunities (
                    event_id, observed_at_utc, trader, market, outcome, whale_side,
                    whale_size_usdc, price, condition_id, outcome_index, transaction_hash,
                    source_timestamp, opportunity_age_sec, trader_resolved_count,
                    trader_win_rate, daily_losses_for_trader, daily_deploy_for_trader,
                    bankroll, deployed_cap_pct, open_positions_count, median_whale_size,
                    conviction, perf_mult, dynamic_max_bet, recommended_size,
                    copied_size_usdc, copy_shares, position_id, decision, decision_reason,
                    is_crypto, is_spread, is_futures, price_capped, duplicate_game,
                    bayes_posterior_mean, bayes_lower_bound, shadow_model_score, shadow_model_decision,
                    base_game, resolution_status, resolved_pnl, resolved_at_utc
                ) VALUES (
                    :event_id, :observed_at_utc, :trader, :market, :outcome, :whale_side,
                    :whale_size_usdc, :price, :condition_id, :outcome_index, :transaction_hash,
                    :source_timestamp, :opportunity_age_sec, :trader_resolved_count,
                    :trader_win_rate, :daily_losses_for_trader, :daily_deploy_for_trader,
                    :bankroll, :deployed_cap_pct, :open_positions_count, :median_whale_size,
                    :conviction, :perf_mult, :dynamic_max_bet, :recommended_size,
                    :copied_size_usdc, :copy_shares, :position_id, :decision, :decision_reason,
                    :is_crypto, :is_spread, :is_futures, :price_capped, :duplicate_game,
                    :bayes_posterior_mean, :bayes_lower_bound, :shadow_model_score, :shadow_model_decision,
                    :base_game, :resolution_status, :resolved_pnl, :resolved_at_utc
                )
                ON CONFLICT(event_id) DO UPDATE SET
                    observed_at_utc=excluded.observed_at_utc,
                    trader=excluded.trader,
                    market=excluded.market,
                    outcome=excluded.outcome,
                    whale_side=excluded.whale_side,
                    whale_size_usdc=excluded.whale_size_usdc,
                    price=excluded.price,
                    condition_id=excluded.condition_id,
                    outcome_index=excluded.outcome_index,
                    transaction_hash=excluded.transaction_hash,
                    source_timestamp=excluded.source_timestamp,
                    opportunity_age_sec=excluded.opportunity_age_sec,
                    trader_resolved_count=excluded.trader_resolved_count,
                    trader_win_rate=excluded.trader_win_rate,
                    daily_losses_for_trader=excluded.daily_losses_for_trader,
                    daily_deploy_for_trader=excluded.daily_deploy_for_trader,
                    bankroll=excluded.bankroll,
                    deployed_cap_pct=excluded.deployed_cap_pct,
                    open_positions_count=excluded.open_positions_count,
                    median_whale_size=excluded.median_whale_size,
                    conviction=excluded.conviction,
                    perf_mult=excluded.perf_mult,
                    dynamic_max_bet=excluded.dynamic_max_bet,
                    recommended_size=excluded.recommended_size,
                    copied_size_usdc=excluded.copied_size_usdc,
                    copy_shares=excluded.copy_shares,
                    position_id=excluded.position_id,
                    decision=excluded.decision,
                    decision_reason=excluded.decision_reason,
                    is_crypto=excluded.is_crypto,
                    is_spread=excluded.is_spread,
                    is_futures=excluded.is_futures,
                    price_capped=excluded.price_capped,
                    duplicate_game=excluded.duplicate_game,
                    bayes_posterior_mean=excluded.bayes_posterior_mean,
                    bayes_lower_bound=excluded.bayes_lower_bound,
                    shadow_model_score=excluded.shadow_model_score,
                    shadow_model_decision=excluded.shadow_model_decision,
                    base_game=excluded.base_game,
                    resolution_status=excluded.resolution_status,
                    resolved_pnl=excluded.resolved_pnl,
                    resolved_at_utc=excluded.resolved_at_utc
                """,
                payload,
            )

    def load_opportunities_for_position(self, position_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM opportunities
                WHERE position_id = ?
                ORDER BY observed_at_utc
                """,
                (position_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_opportunity_resolution(
        self,
        position_id: str | None,
        status: str,
        pnl: float | None,
        resolved_at_utc: str,
        *,
        event_id: str | None = None,
    ):
        with self._lock, self._connect() as conn:
            if event_id:
                conn.execute(
                    """
                    UPDATE opportunities
                       SET resolution_status = ?,
                           resolved_pnl = ?,
                           resolved_at_utc = ?
                     WHERE event_id = ?
                    """,
                    (status, pnl, resolved_at_utc, event_id),
                )
            elif position_id:
                conn.execute(
                    """
                    UPDATE opportunities
                       SET resolution_status = ?,
                           resolved_pnl = ?,
                           resolved_at_utc = ?
                     WHERE position_id = ?
                    """,
                    (status, pnl, resolved_at_utc, position_id),
                )

    def load_unresolved_opportunities(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM opportunities
                WHERE resolution_status IS NULL
                ORDER BY observed_at_utc
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_trader_stats(self, trader: str, wins: int, losses: int):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trader_stats (trader, wins, losses)
                VALUES (?, ?, ?)
                ON CONFLICT(trader) DO UPDATE SET
                    wins=excluded.wins,
                    losses=excluded.losses
                """,
                (trader, wins, losses),
            )

    def set_daily_risk(self, day_utc: str, gross_wins: float, gross_losses: float):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_risk (day_utc, gross_wins, gross_losses)
                VALUES (?, ?, ?)
                ON CONFLICT(day_utc) DO UPDATE SET
                    gross_wins=excluded.gross_wins,
                    gross_losses=excluded.gross_losses
                """,
                (day_utc, gross_wins, gross_losses),
            )

    def set_value(self, key: str, value):
        if not isinstance(value, str):
            value = json.dumps(value)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def get_value(self, key: str, default=None):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv_state WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return default
        value = row["value"]
        try:
            return json.loads(value)
        except Exception:
            return value

    def load_runtime_state(self) -> dict:
        with self._connect() as conn:
            positions = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM positions"
                ).fetchall()
            ]
            trader_stats = {
                row["trader"]: {"wins": row["wins"], "losses": row["losses"]}
                for row in conn.execute("SELECT * FROM trader_stats").fetchall()
            }
            daily_rows = {
                row["day_utc"]: {
                    "gross_wins": row["gross_wins"],
                    "gross_losses": row["gross_losses"],
                }
                for row in conn.execute("SELECT * FROM daily_risk").fetchall()
            }
            fills = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM copied_fills ORDER BY timestamp_utc"
                ).fetchall()
            ]
            opportunities = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM opportunities ORDER BY observed_at_utc"
                ).fetchall()
            ]
        return {
            "positions": positions,
            "trader_stats": trader_stats,
            "daily_risk": daily_rows,
            "fills": fills,
            "opportunities": opportunities,
            "closed_pnl": float(self.get_value("closed_pnl", 0.0) or 0.0),
            "wins": int(self.get_value("wins", 0) or 0),
            "losses": int(self.get_value("losses", 0) or 0),
            "daily_losses_per_trader": self.get_value("daily_losses_per_trader", {}) or {},
            "daily_deploy_per_trader": self.get_value("daily_deploy_per_trader", {}) or {},
            "milestones_reached": set(self.get_value("milestones_reached", []) or []),
            "whale_sizes": self.get_value("whale_sizes", []) or [],
            "budget_day_utc": self.get_value("budget_day_utc", ""),
            "health": self.get_value("health", {}) or {},
        }
