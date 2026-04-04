from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class TraderPosterior:
    trader: str
    wins: int
    losses: int
    alpha: float
    beta: float
    posterior_mean: float
    lower_bound: float
    upper_bound: float
    prior_mean: float
    prior_strength: float

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def observed_win_rate(self) -> float:
        return (self.wins / self.total) if self.total else 0.0


def estimate_beta_prior(
    trader_stats: dict[str, dict[str, int]],
    *,
    default_mean: float = 0.55,
    default_strength: float = 8.0,
) -> tuple[float, float]:
    totals = []
    weighted_wins = 0
    weighted_total = 0
    for stats in trader_stats.values():
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)
        total = wins + losses
        if total <= 0:
            continue
        totals.append(total)
        weighted_wins += wins
        weighted_total += total

    if weighted_total <= 0:
        return default_mean * default_strength, (1.0 - default_mean) * default_strength

    prior_mean = weighted_wins / weighted_total
    avg_total = sum(totals) / len(totals)
    prior_strength = min(25.0, max(default_strength, avg_total * 0.35))
    return prior_mean * prior_strength, (1.0 - prior_mean) * prior_strength


def compute_posterior(
    wins: int,
    losses: int,
    *,
    alpha_prior: float,
    beta_prior: float,
    cred_level: float = 0.90,
    trader: str = "",
) -> TraderPosterior:
    wins = int(wins or 0)
    losses = int(losses or 0)
    alpha = alpha_prior + wins
    beta = beta_prior + losses
    mean = alpha / (alpha + beta)
    variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))
    z = NormalDist().inv_cdf((1.0 + cred_level) / 2.0)
    margin = z * (variance ** 0.5)
    lower = max(0.0, mean - margin)
    upper = min(1.0, mean + margin)
    prior_total = alpha_prior + beta_prior
    prior_mean = alpha_prior / prior_total if prior_total > 0 else 0.5
    return TraderPosterior(
        trader=trader,
        wins=wins,
        losses=losses,
        alpha=alpha,
        beta=beta,
        posterior_mean=mean,
        lower_bound=lower,
        upper_bound=upper,
        prior_mean=prior_mean,
        prior_strength=prior_total,
    )


def rank_trader_posteriors(
    trader_stats: dict[str, dict[str, int]],
    *,
    default_mean: float = 0.55,
    default_strength: float = 8.0,
    cred_level: float = 0.90,
) -> list[TraderPosterior]:
    alpha_prior, beta_prior = estimate_beta_prior(
        trader_stats,
        default_mean=default_mean,
        default_strength=default_strength,
    )
    rows = []
    for trader, stats in trader_stats.items():
        rows.append(
            compute_posterior(
                int(stats.get("wins", 0) or 0),
                int(stats.get("losses", 0) or 0),
                alpha_prior=alpha_prior,
                beta_prior=beta_prior,
                cred_level=cred_level,
                trader=trader,
            )
        )
    rows.sort(
        key=lambda row: (row.lower_bound, row.posterior_mean, row.total),
        reverse=True,
    )
    return rows
