import tempfile
import unittest
from pathlib import Path

from worldcup_api_spike import (
    BookSample,
    EventSummary,
    MarketSummary,
    WorldCupSpikeResult,
)
from worldcup_recommendations import WorldCupRecommendationStore, recommend_from_edge
from worldcup_report import render_worldcup_report_html, write_worldcup_report
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


class WorldCupReportTests(unittest.TestCase):
    def test_render_worldcup_report_html_shows_summary_rows_and_escapes_notes(self):
        recommendations = [
            {
                "recommendation_id": "rec-1",
                "created_at_utc": "2026-06-30 12:05:00",
                "status": "WATCH",
                "question": "Will Brazil win the 2026 FIFA World Cup?",
                "outcome": "Yes",
                "user_probability": 0.14,
                "midpoint": 0.111,
                "edge": 0.029,
                "resolution_result": "WON",
                "brier_score": 0.7396,
                "market_brier_score": 0.790321,
                "brier_edge": 0.050721,
                "thesis": "Brazil <script>alert(1)</script> depth.",
                "resolution_note": "settled locally",
            }
        ]
        summary = {
            "resolved_count": 1,
            "average_brier_score": 0.7396,
            "average_market_brier_score": 0.790321,
            "average_brier_edge": 0.050721,
        }

        html = render_worldcup_report_html(
            recommendations,
            summary,
            generated_at_utc="2026-07-01 12:00:00",
        )

        self.assertIn("World Cup Paper Recommendation Report", html)
        self.assertIn("Paper-only local report", html)
        self.assertIn("Resolved Recommendations", html)
        self.assertIn("rec-1", html)
        self.assertIn("WON", html)
        self.assertIn("+0.051", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_write_worldcup_report_loads_db_and_writes_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worldcup.db"
            output_path = Path(tmpdir) / "worldcup_report.html"
            WorldCupSnapshotStore(db_path).save_result(
                _sample_result(),
                captured_at_utc="2026-06-30 12:00:00",
                snapshot_id="snap-1",
            )
            recommend_from_edge(
                db_path=db_path,
                probabilities={"token-brazil-yes": 0.14},
                token_id="token-brazil-yes",
                thesis="Brazil price underrates squad depth.",
                recommendation_id="rec-1",
                created_at_utc="2026-06-30 12:05:00",
            )
            WorldCupRecommendationStore(db_path).resolve_recommendation(
                "rec-1",
                result="WON",
                resolved_at_utc="2026-07-20 20:00:00",
                note="settled by market result",
            )

            written = write_worldcup_report(
                db_path=db_path,
                output_path=output_path,
                generated_at_utc="2026-07-01 12:00:00",
            )
            html = output_path.read_text(encoding="utf-8")

        self.assertEqual(written, output_path)
        self.assertIn("World Cup Paper Recommendation Report", html)
        self.assertIn("Avg Brier Edge", html)
        self.assertIn("Brazil price underrates squad depth.", html)


if __name__ == "__main__":
    unittest.main()
