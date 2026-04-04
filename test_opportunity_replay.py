import unittest

from opportunity_replay import normalized_pnl_per_dollar, simulate_event_driven_policy
from shadow_model import OnlineLogisticModel


class OpportunityReplayTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
