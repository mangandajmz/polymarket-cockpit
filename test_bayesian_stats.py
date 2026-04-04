import unittest

from bayesian_stats import estimate_beta_prior, rank_trader_posteriors


class BayesianStatsTests(unittest.TestCase):
    def test_empirical_bayes_shrinks_small_samples(self):
        trader_stats = {
            "hot_streak": {"wins": 3, "losses": 0},
            "proven": {"wins": 70, "losses": 30},
            "slump": {"wins": 10, "losses": 20},
        }

        ranked = {row.trader: row for row in rank_trader_posteriors(trader_stats)}

        self.assertLess(ranked["hot_streak"].posterior_mean, 1.0)
        self.assertLess(ranked["hot_streak"].lower_bound, ranked["proven"].lower_bound)
        self.assertGreater(ranked["proven"].lower_bound, 0.5)

    def test_prior_estimate_tracks_population_mean(self):
        trader_stats = {
            "a": {"wins": 8, "losses": 2},
            "b": {"wins": 6, "losses": 4},
        }
        alpha, beta = estimate_beta_prior(trader_stats, default_mean=0.55, default_strength=8.0)
        prior_mean = alpha / (alpha + beta)
        self.assertGreater(prior_mean, 0.65)
        self.assertLess(prior_mean, 0.75)


if __name__ == "__main__":
    unittest.main()
