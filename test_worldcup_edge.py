import tempfile
import unittest
from pathlib import Path

from worldcup_api_spike import (
    BookSample,
    EventSummary,
    MarketSummary,
    WorldCupSpikeResult,
)
from worldcup_edge import build_edge_board, format_edge_table, load_probability_file
from worldcup_snapshot import WorldCupSnapshotStore


def _sample_result():
    return WorldCupSpikeResult(
        query="2026 FIFA World Cup",
        events=[
            EventSummary(
                id="event-1",
                title="2026 FIFA World Cup",
                slug="2026-fifa-world-cup",
            )
        ],
        markets=[
            MarketSummary(
                id="market-1",
                question="Will Brazil win the 2026 FIFA World Cup?",
                condition_id="cond-1",
                outcomes=["Yes", "No"],
                clob_token_ids=["token-brazil-yes", "token-brazil-no"],
                active=True,
                closed=False,
                enable_order_book=True,
                is_combo_candidate=False,
            ),
            MarketSummary(
                id="market-2",
                question="Will France win the 2026 FIFA World Cup?",
                condition_id="cond-2",
                outcomes=["Yes", "No"],
                clob_token_ids=["token-france-yes", "token-france-no"],
                active=True,
                closed=False,
                enable_order_book=True,
                is_combo_candidate=False,
            ),
        ],
        token_count=4,
        sample_books=[
            BookSample(
                token_id="token-brazil-yes",
                best_bid=0.110,
                best_ask=0.112,
                midpoint=0.111,
            ),
            BookSample(
                token_id="token-france-yes",
                best_bid=0.230,
                best_ask=0.238,
                midpoint=0.234,
            ),
        ],
        combo_candidate_count=0,
        wallet_auth_used=False,
        unsigned_rfq_quote_supported=None,
    )


class WorldCupEdgeTests(unittest.TestCase):
    def test_load_probability_file_normalizes_decimal_and_percent_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "probabilities.csv"
            path.write_text(
                "token_id,user_probability,note\n"
                "token-brazil-yes,0.14,model lean\n"
                "token-france-yes,27,manual percent\n",
                encoding="utf-8",
            )

            probabilities = load_probability_file(path)

        self.assertEqual(probabilities["token-brazil-yes"].user_probability, 0.14)
        self.assertEqual(probabilities["token-france-yes"].user_probability, 0.27)
        self.assertEqual(probabilities["token-brazil-yes"].note, "model lean")

    def test_build_edge_board_ranks_edges_and_filters_wide_spreads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worldcup.db"
            store = WorldCupSnapshotStore(db_path)
            store.save_result(
                _sample_result(),
                captured_at_utc="2026-06-30 12:00:00",
                snapshot_id="snap-1",
            )
            probabilities = {
                "token-brazil-yes": 0.14,
                "token-france-yes": 0.30,
            }

            rows = build_edge_board(store, probabilities, max_spread=0.003)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_id, "token-brazil-yes")
        self.assertAlmostEqual(rows[0].edge, 0.029)
        self.assertEqual(rows[0].rank, 1)

    def test_format_edge_table_is_operator_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worldcup.db"
            store = WorldCupSnapshotStore(db_path)
            store.save_result(
                _sample_result(),
                captured_at_utc="2026-06-30 12:00:00",
                snapshot_id="snap-1",
            )
            rows = build_edge_board(store, {"token-brazil-yes": 0.14})

        table = format_edge_table(rows)

        self.assertIn("Rank", table)
        self.assertIn("Brazil", table)
        self.assertIn("0.140", table)
        self.assertIn("+0.029", table)


if __name__ == "__main__":
    unittest.main()
