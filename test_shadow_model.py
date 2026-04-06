import math
import unittest

from shadow_model import OnlineLogisticModel, _safe_float, feature_vector


class ShadowModelTests(unittest.TestCase):
    def test_safe_float_rejects_nan_and_inf(self):
        self.assertEqual(_safe_float(float("nan"), 7.0), 7.0)
        self.assertEqual(_safe_float(float("inf"), 7.0), 7.0)
        self.assertEqual(_safe_float(float("-inf"), 7.0), 7.0)

    def test_feature_vector_sanitizes_nan_inputs(self):
        row = {
            "price": float("nan"),
            "whale_size_usdc": float("nan"),
            "opportunity_age_sec": float("nan"),
            "conviction": float("nan"),
            "trader_win_rate": float("nan"),
            "trader_resolved_count": float("nan"),
            "bankroll": float("nan"),
            "deployed_cap_pct": float("nan"),
            "daily_losses_for_trader": float("nan"),
            "open_positions_count": float("nan"),
        }
        vector = feature_vector(row)
        self.assertTrue(all(not math.isnan(value) and not math.isinf(value) for value in vector))

    def test_model_outputs_finite_probabilities_with_nan_inputs(self):
        model = OnlineLogisticModel()
        bad_row = {
            "price": float("nan"),
            "whale_size_usdc": float("nan"),
            "opportunity_age_sec": float("nan"),
            "conviction": float("nan"),
            "trader_win_rate": float("nan"),
            "trader_resolved_count": float("nan"),
            "bankroll": float("nan"),
            "deployed_cap_pct": float("nan"),
            "daily_losses_for_trader": float("nan"),
            "open_positions_count": float("nan"),
        }
        p0 = model.predict_proba(bad_row)
        model.update(bad_row, 1)
        p1 = model.predict_proba(bad_row)
        self.assertTrue(0.0 <= p0 <= 1.0)
        self.assertTrue(0.0 <= p1 <= 1.0)
        self.assertFalse(math.isnan(p0) or math.isnan(p1))


if __name__ == "__main__":
    unittest.main()
