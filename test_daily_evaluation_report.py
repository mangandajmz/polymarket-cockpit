import unittest
from datetime import datetime, timedelta, timezone

from daily_evaluation_report import build_report, filter_rows, parse_ts


class DailyEvaluationReportTests(unittest.TestCase):
    def test_parse_ts_accepts_datetime_like_values(self):
        value = datetime(2026, 4, 5, 12, 30, 0)
        self.assertEqual(parse_ts(value), value)

    def test_parse_ts_normalizes_aware_values_to_naive_utc(self):
        value = datetime(2026, 4, 5, 12, 30, 0, tzinfo=timezone.utc)
        parsed = parse_ts(value)
        self.assertEqual(parsed, datetime(2026, 4, 5, 12, 30, 0))
        self.assertIsNone(parsed.tzinfo)

    def test_filter_rows_respects_lookback(self):
        rows = [
            {"event_id": "old", "observed_at_utc": "2026-04-03 00:00:00"},
            {"event_id": "new", "observed_at_utc": "2026-04-05 00:00:00"},
        ]
        kept, cutoff = filter_rows(rows, lookback_days=1.0, now=datetime(2026, 4, 5, 12, 0, 0))
        self.assertEqual(cutoff.strftime("%Y-%m-%d %H:%M:%S"), "2026-04-04 12:00:00")
        self.assertEqual([row["event_id"] for row in kept], ["new"])

    def test_filter_rows_handles_mixed_naive_and_aware_timestamps(self):
        rows = [
            {"event_id": "old", "observed_at_utc": datetime(2026, 4, 4, 18, 59, 59, tzinfo=timezone.utc)},
            {"event_id": "new", "observed_at_utc": datetime(2026, 4, 4, 19, 0, 1, tzinfo=timezone.utc)},
            {"event_id": "naive", "observed_at_utc": "2026-04-04 20:00:00"},
        ]
        kept, _ = filter_rows(rows, lookback_days=1.0, now=datetime(2026, 4, 5, 19, 0, 0))
        self.assertEqual([row["event_id"] for row in kept], ["new", "naive"])

    def test_build_report_counts_selection_and_replay_inputs(self):
        now = datetime.utcnow()
        t0 = now.replace(microsecond=0, second=0, minute=0)
        t1 = t0 + timedelta(hours=1)
        t2 = t0 + timedelta(hours=2)
        t3 = t0 + timedelta(hours=3)
        rows = [
            {
                "event_id": "a",
                "observed_at_utc": t0.strftime("%Y-%m-%d %H:%M:%S"),
                "resolved_at_utc": t1.strftime("%Y-%m-%d %H:%M:%S"),
                "trader": "alice",
                "decision": "COPIED",
                "decision_reason": "copied",
                "shadow_model_decision": "TAKE",
                "shadow_model_score": 0.70,
                "bayes_posterior_mean": 0.62,
                "bayes_lower_bound": 0.61,
                "resolution_status": "WIN",
                "whale_side": "BUY",
                "whale_size_usdc": 1000.0,
                "price": 0.50,
                "is_crypto": 0,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
            },
            {
                "event_id": "b",
                "observed_at_utc": t2.strftime("%Y-%m-%d %H:%M:%S"),
                "resolved_at_utc": t3.strftime("%Y-%m-%d %H:%M:%S"),
                "trader": "bob",
                "decision": "SKIP",
                "decision_reason": "price_cap",
                "shadow_model_decision": "SKIP",
                "shadow_model_score": 0.42,
                "bayes_posterior_mean": 0.48,
                "bayes_lower_bound": 0.39,
                "resolution_status": "LOSS",
                "whale_side": "BUY",
                "whale_size_usdc": 1100.0,
                "price": 0.60,
                "is_crypto": 0,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
            },
        ]

        report = build_report(rows, lookback_days=1.0)
        self.assertEqual(report["coverage"]["opportunities"], 2)
        self.assertEqual(report["coverage"]["resolved"], 2)
        self.assertEqual(report["selection"]["heuristic"][0], 1)
        self.assertEqual(report["selection"]["bayes"][0], 1)
        self.assertEqual(report["selection"]["model"][0], 1)
        self.assertIn("model_take_rate", report["selection"])
        self.assertIn("hybrid", report["replay"])
        self.assertIn("model_threshold_sweep", report["replay"])
        self.assertIn("hybrid_threshold_sweep", report["replay"])
        self.assertIn("model_diagnostics", report["replay"])
        self.assertIn("best_hybrid_threshold", report["replay"])
        self.assertGreaterEqual(report["replay"]["current"]["final_bankroll"], 300.0)


if __name__ == "__main__":
    unittest.main()
