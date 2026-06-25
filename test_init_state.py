import sqlite3

from init_state import initialize_state
from state_store import StateStore


def test_initialize_state_creates_schema_and_health_defaults(tmp_path):
    db_path = tmp_path / "bot_state.db"

    initialize_state(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        daily = conn.execute("SELECT COUNT(*) FROM daily_risk").fetchone()[0]

    store = StateStore(db_path)
    health = store.get_value("health")

    assert "positions" in tables
    assert "opportunities" in tables
    assert "recommendations" in tables
    assert daily == 1
    assert health["status_msg"] == "Initialized; paper bot has not started polling yet."
    assert store.get_value("invariant_issues") == []


def test_initialize_state_preserves_existing_runtime_values(tmp_path):
    db_path = tmp_path / "bot_state.db"
    store = StateStore(db_path)
    store.set_value("health", {"status_msg": "Live", "api_fail_count": 2})
    store.set_value("wins", 3)

    initialize_state(db_path)

    restored = StateStore(db_path)
    assert restored.get_value("health") == {"status_msg": "Live", "api_fail_count": 2}
    assert restored.get_value("wins") == 3
