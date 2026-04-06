import unittest
from datetime import datetime, timezone

from opportunity_replay import (
    analyze_model_replay,
    _parse_ts,
    normalized_pnl_per_dollar,
    simulate_event_driven_policy,
    simulate_model_threshold_sweep,
)
from shadow_model import OnlineLogisticModel


class OpportunityReplayTests(unittest.TestCase):
    def test_parse_ts_accepts_aware_datetimes(self):
        parsed = _parse_ts(datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(parsed, datetime(2026, 4, 5, 12, 0, 0))
        self.assertIsNone(parsed.tzinfo)

    def test_normalized_pnl_per_dollar_matches_binary_market_payoff(self):
        win_row = {"price": 0.40, "resolution_status": "WIN"}
        loss_row = {"price": 0.40, "resolution_status": "LOSS"}

        self.assertAlmostEqual(normalized_pnl_per_dollar(win_row), 1.5)
        self.assertAlmostEqual(normalized_pnl_per_dollar(loss_row), -1.0)

    def test_event_driven_policy_releases_capital_on_resolution(self):
        rows = [
            {
                "event_id": "a",
                "trader": "alice",
                "decision": "COPIED",
                "price": 0.50,
                "resolution_status": "WIN",
                "observed_at_utc": "2026-04-01 00:00:00",
                "resolved_at_utc": "2026-04-01 01:00:00",
            },
            {
                "event_id": "b",
                "trader": "alice",
                "decision": "COPIED",
                "price": 0.50,
                "resolution_status": "WIN",
                "observed_at_utc": "2026-04-01 02:00:00",
                "resolved_at_utc": "2026-04-01 03:00:00",
            },
        ]

        metrics = simulate_event_driven_policy(
            rows,
            policy_name="current",
            start_bankroll=10.0,
            unit_stake=10.0,
        )

        self.assertEqual(metrics["trades_taken"], 2)
        self.assertAlmostEqual(metrics["final_bankroll"], 30.0)
        self.assertAlmostEqual(metrics["max_locked_capital"], 10.0)

    def test_event_driven_policy_handles_mixed_timestamp_types(self):
        rows = [
            {
                "event_id": "a",
                "trader": "alice",
                "decision": "COPIED",
                "price": 0.50,
                "resolution_status": "WIN",
                "observed_at_utc": datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc),
                "resolved_at_utc": datetime(2026, 4, 1, 1, 0, 0, tzinfo=timezone.utc),
            },
            {
                "event_id": "b",
                "trader": "alice",
                "decision": "COPIED",
                "price": 0.50,
                "resolution_status": "WIN",
                "observed_at_utc": "2026-04-01 02:00:00",
                "resolved_at_utc": "2026-04-01 03:00:00",
            },
        ]

        metrics = simulate_event_driven_policy(
            rows,
            policy_name="current",
            start_bankroll=10.0,
            unit_stake=10.0,
        )

        self.assertEqual(metrics["trades_taken"], 2)
        self.assertAlmostEqual(metrics["final_bankroll"], 30.0)

    def test_online_model_learns_toward_observed_labels(self):
        model = OnlineLogisticModel()
        win_row = {
            "price": 0.40,
            "whale_size_usdc": 2000.0,
            "opportunity_age_sec": 10,
            "conviction": 1.5,
            "trader_win_rate": 70.0,
            "trader_resolved_count": 20,
            "bankroll": 300.0,
            "deployed_cap_pct": 0.1,
            "daily_losses_for_trader": 0,
            "open_positions_count": 1,
        }
        loss_row = dict(win_row)
        loss_row["price"] = 0.78
        for _ in range(25):
            model.update(win_row, 1)
            model.update(loss_row, 0)
        after_win = model.predict_proba(win_row)
        after_loss = model.predict_proba(loss_row)
        self.assertGreater(after_win, after_loss)
        self.assertEqual(model.examples_seen, 50)

    def test_threshold_sweep_returns_metrics_per_threshold(self):
        rows = []
        for idx in range(20):
            is_win = idx % 2 == 0
            rows.append({
                "event_id": f"e{idx}",
                "trader": "alice" if idx < 10 else "bob",
                "decision": "SKIP",
                "price": 0.45 if is_win else 0.75,
                "resolution_status": "WIN" if is_win else "LOSS",
                "observed_at_utc": f"2026-04-01 {idx:02d}:00:00",
                "resolved_at_utc": f"2026-04-02 {idx:02d}:00:00",
                "whale_size_usdc": 1500.0,
                "whale_side": "BUY",
                "is_crypto": 0,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
                "opportunity_age_sec": 10,
                "conviction": 1.2,
                "trader_win_rate": 60.0,
                "trader_resolved_count": 30,
                "bankroll": 300.0,
                "deployed_cap_pct": 0.1,
                "daily_losses_for_trader": 0,
                "open_positions_count": 1,
            })

        sweep = simulate_model_threshold_sweep(rows, thresholds=[0.55, 0.65])
        self.assertEqual([row["model_threshold"] for row in sweep], [0.55, 0.65])
        for row in sweep:
            self.assertIn("bankroll_delta", row)
            self.assertIn("return_per_locked_dollar_hour", row)

    def test_analyze_model_replay_reports_warmup_and_logged_comparison(self):
        rows = []
        for idx in range(12):
            is_win = idx % 2 == 0
            rows.append({
                "event_id": f"diag{idx}",
                "trader": "alice",
                "decision": "SKIP",
                "shadow_model_decision": "TAKE" if idx >= 10 else "SKIP",
                "price": 0.45 if is_win else 0.75,
                "resolution_status": "WIN" if is_win else "LOSS",
                "observed_at_utc": f"2026-04-01 {idx:02d}:00:00",
                "resolved_at_utc": f"2026-04-02 {idx:02d}:00:00",
                "whale_size_usdc": 1500.0,
                "whale_side": "BUY",
                "is_crypto": 0,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
                "opportunity_age_sec": 10,
                "conviction": 1.2,
                "trader_win_rate": 60.0,
                "trader_resolved_count": 30,
                "bankroll": 300.0,
                "deployed_cap_pct": 0.1,
                "daily_losses_for_trader": 0,
                "open_positions_count": 1,
            })

        diag = analyze_model_replay(rows)
        self.assertEqual(diag["parsed_rows"], 12)
        self.assertEqual(diag["warm_rows"], 2)
        self.assertEqual(diag["logged_take_count"], 2)
        self.assertIsNotNone(diag["first_warm_event_id"])


if __name__ == "__main__":
    unittest.main()
