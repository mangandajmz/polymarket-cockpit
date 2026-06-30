import tempfile
import unittest
from pathlib import Path

from worldcup_api_spike import (
    BookSample,
    EventSummary,
    MarketSummary,
    WorldCupSpikeResult,
)
from worldcup_recommendations import (
    WorldCupRecommendationStore,
    format_recommendations_table,
    recommend_from_edge,
)
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
            )
        ],
        token_count=2,
        sample_books=[
            BookSample(
                token_id="token-brazil-yes",
                best_bid=0.110,
                best_ask=0.112,
                midpoint=0.111,
            )
        ],
        combo_candidate_count=0,
        wallet_auth_used=False,
        unsigned_rfq_quote_supported=None,
    )


class WorldCupRecommendationTests(unittest.TestCase):
    def test_recommend_from_edge_persists_paper_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worldcup.db"
            snapshot_store = WorldCupSnapshotStore(db_path)
            snapshot_store.save_result(
                _sample_result(),
                captured_at_utc="2026-06-30 12:00:00",
                snapshot_id="snap-1",
            )

            record = recommend_from_edge(
                db_path=db_path,
                probabilities={"token-brazil-yes": 0.14},
                token_id="token-brazil-yes",
                thesis="Brazil price underrates squad depth.",
                status="WATCH",
                recommendation_id="rec-1",
                created_at_utc="2026-06-30 12:05:00",
            )
            rows = WorldCupRecommendationStore(db_path).load_recommendations()

        self.assertEqual(record.recommendation_id, "rec-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recommendation_id"], "rec-1")
        self.assertEqual(rows[0]["token_id"], "token-brazil-yes")
        self.assertEqual(rows[0]["question"], "Will Brazil win the 2026 FIFA World Cup?")
        self.assertEqual(rows[0]["outcome"], "Yes")
        self.assertEqual(rows[0]["status"], "WATCH")
        self.assertEqual(rows[0]["thesis"], "Brazil price underrates squad depth.")
        self.assertAlmostEqual(rows[0]["user_probability"], 0.14)
        self.assertAlmostEqual(rows[0]["midpoint"], 0.111)
        self.assertAlmostEqual(rows[0]["edge"], 0.029)

    def test_recommend_from_edge_rejects_blank_thesis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worldcup.db"
            WorldCupSnapshotStore(db_path).save_result(
                _sample_result(),
                captured_at_utc="2026-06-30 12:00:00",
                snapshot_id="snap-1",
            )

            with self.assertRaises(ValueError):
                recommend_from_edge(
                    db_path=db_path,
                    probabilities={"token-brazil-yes": 0.14},
                    token_id="token-brazil-yes",
                    thesis=" ",
                )

    def test_format_recommendations_table_is_operator_readable(self):
        rows = [
            {
                "created_at_utc": "2026-06-30 12:05:00",
                "status": "WATCH",
                "question": "Will Brazil win the 2026 FIFA World Cup?",
                "outcome": "Yes",
                "user_probability": 0.14,
                "midpoint": 0.111,
                "edge": 0.029,
                "thesis": "Brazil price underrates squad depth.",
            }
        ]

        table = format_recommendations_table(rows)

        self.assertIn("Status", table)
        self.assertIn("WATCH", table)
        self.assertIn("Brazil", table)
        self.assertIn("+0.029", table)
        self.assertIn("squad depth", table)


if __name__ == "__main__":
    unittest.main()
