import sqlite3

from property_status import build_status


def test_build_status_without_state_db_is_red(tmp_path):
    status = build_status(tmp_path / "missing.db")

    assert status["schema_version"] == "property_status.v1"
    assert status["property_id"] == "polymarket_cockpit"
    assert status["status"] == "RED"
    assert status["top_issue"]["severity"] == "RED"
    assert status["kpis"]["state_db_present"] == 0


def test_build_status_counts_recommendation_state(tmp_path):
    db_path = tmp_path / "bot_state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE recommendations (status TEXT, resolution_status TEXT)")
        conn.execute("CREATE TABLE positions (status TEXT)")
        conn.execute("CREATE TABLE opportunities (resolution_status TEXT)")
        conn.executemany(
            "INSERT INTO recommendations VALUES (?, ?)",
            [("RECOMMEND", None), ("WATCH", None), ("AVOID", None), ("RECOMMEND", "WIN")],
        )
        conn.executemany("INSERT INTO positions VALUES (?)", [("OPEN",), ("WIN",)])
        conn.executemany("INSERT INTO opportunities VALUES (?)", [(None,), ("LOSS",)])

    status = build_status(db_path)

    assert status["status"] == "GREEN"
    assert status["top_issue"] is None
    assert status["kpis"]["recommendations_total"] == 4
    assert status["kpis"]["open_recommendations"] == 2
    assert status["kpis"]["avoid_recommendations"] == 1
    assert status["kpis"]["open_positions"] == 1
    assert status["kpis"]["unresolved_opportunities"] == 1
