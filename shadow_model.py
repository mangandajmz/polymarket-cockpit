from __future__ import annotations

import math


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def feature_vector(row: dict) -> list[float]:
    price = _safe_float(row.get("price"))
    whale_size = max(1.0, _safe_float(row.get("whale_size_usdc"), 1.0))
    age = max(0.0, _safe_float(row.get("opportunity_age_sec")))
    conviction = _safe_float(row.get("conviction"))
    trader_wr = _safe_float(row.get("trader_win_rate")) / 100.0
    trader_n = max(0.0, _safe_float(row.get("trader_resolved_count")))
    bankroll = max(1.0, _safe_float(row.get("bankroll"), 1.0))
    deploy_pct = _safe_float(row.get("deployed_cap_pct"))
    daily_losses = _safe_float(row.get("daily_losses_for_trader"))
    open_positions = _safe_float(row.get("open_positions_count"))

    return [
        1.0,
        price,
        math.log(whale_size),
        age / 300.0,
        conviction,
        trader_wr,
        min(trader_n, 50.0) / 50.0,
        math.log(bankroll) / 10.0,
        deploy_pct,
        daily_losses / 5.0,
        open_positions / 10.0,
    ]


class OnlineLogisticModel:
    def __init__(self, learning_rate: float = 0.15, l2: float = 0.001):
        self.learning_rate = learning_rate
        self.l2 = l2
        self.weights: list[float] | None = None
        self.examples_seen = 0

    def predict_proba(self, row: dict) -> float:
        x = feature_vector(row)
        if self.weights is None:
            self.weights = [0.0] * len(x)
        score = sum(w * xi for w, xi in zip(self.weights, x))
        if score >= 0:
            z = math.exp(-score)
            return 1.0 / (1.0 + z)
        z = math.exp(score)
        return z / (1.0 + z)

    def update(self, row: dict, label: int):
        x = feature_vector(row)
        if self.weights is None:
            self.weights = [0.0] * len(x)
        p = self.predict_proba(row)
        error = p - float(label)
        for i, xi in enumerate(x):
            grad = error * xi + self.l2 * self.weights[i]
            self.weights[i] -= self.learning_rate * grad
        self.examples_seen += 1
