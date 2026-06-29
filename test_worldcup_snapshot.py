import tempfile
import unittest

from worldcup_api_spike import (
    BookSample,
    EventSummary,
    MarketSummary,
    WorldCupSpikeResult,
)
from worldcup_snapshot import (
    ReadOnlyBoundaryError,
    WorldCupSnapshotStore,
    format_odds_table,
)


def _sample_result(wallet_auth_used=False):
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
                clob_token_ids=["token-yes", "token-no"],
                active=True,
                closed=False,
                enable_order_book=True,
                is_combo_candidate=False,
            )
        ],
        token_count=2,
        sample_books=[
            BookSample(
                token_id="token-yes",
                best_bid=0.111,
                best_ask=0.113,
                midpoint=0.112,
            )
        ],
        combo_candidate_count=0,
        wallet_auth_used=wallet_auth_used,
        unsigned_rfq_quote_supported=None,
    )


class WorldCupSnapshotTests(unittest.TestCase):
    def test_store_persists_events_markets_tokens_and_books(self):
        with tempfile_db_path() as db_path:
            store = WorldCupSnapshotStore(db_path)

            summary = store.save_result(
                _sample_result(),
                captured_at_utc="2026-06-29 12:00:00",
                snapshot_id="snap-1",
            )
            rows = store.load_latest_odds()

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["market_count"], 1)
        self.assertEqual(summary["token_count"], 2)
        self.assertEqual(summary["book_snapshot_count"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question"], "Will Brazil win the 2026 FIFA World Cup?")
        self.assertEqual(rows[0]["outcome"], "Yes")
        self.assertEqual(rows[0]["midpoint"], 0.112)
        self.assertAlmostEqual(rows[0]["spread"], 0.002)

    def test_store_rejects_wallet_authenticated_results(self):
        with tempfile_db_path() as db_path:
            store = WorldCupSnapshotStore(db_path)

            with self.assertRaises(ReadOnlyBoundaryError):
                store.save_result(_sample_result(wallet_auth_used=True))

    def test_format_odds_table_is_operator_readable(self):
        rows = [
            {
                "question": "Will Brazil win the 2026 FIFA World Cup?",
                "outcome": "Yes",
                "best_bid": 0.111,
                "best_ask": 0.113,
                "midpoint": 0.112,
                "spread": 0.002,
                "captured_at_utc": "2026-06-29 12:00:00",
            }
        ]

        table = format_odds_table(rows)

        self.assertIn("Question", table)
        self.assertIn("Brazil", table)
        self.assertIn("0.112", table)
        self.assertIn("0.002", table)


class tempfile_db_path:
    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.name = f"{self._tmpdir.name}/snapshot.db"
        return self.name

    def __exit__(self, exc_type, exc_value, traceback):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
