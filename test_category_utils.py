import unittest

from category_utils import classify_market, classify_market_details


class CategoryUtilsTests(unittest.TestCase):
    def test_esports_market_is_classified_as_sports(self):
        market = "Counter-Strike: FUT Esports vs Inner Circle Esport"
        self.assertEqual(classify_market(market), "Sports")

    def test_tennis_matchup_market_is_classified_as_sports(self):
        market = "Miyazaki: Harry Wendelken vs Tung-Lin Wu"
        self.assertEqual(classify_market(market), "Sports")

    def test_politics_market_is_classified_as_politics(self):
        market = "Will Trump win the 2028 presidential election?"
        self.assertEqual(classify_market(market), "Politics")

    def test_crypto_market_is_classified_as_crypto(self):
        market = "Will Bitcoin hit $150k before 2027?"
        self.assertEqual(classify_market(market), "Crypto")

    def test_finance_market_is_classified_as_finance(self):
        market = "Will the Fed cut interest rates in June?"
        self.assertEqual(classify_market(market), "Finance")

    def test_ambiguous_matchup_without_clear_signal_defaults_to_sports(self):
        market = "Alice vs Bob"
        category, score, _ = classify_market_details(market)
        self.assertEqual(category, "Sports")
        self.assertGreaterEqual(score, 3)

    def test_unmatched_market_falls_back_to_other(self):
        market = "Will it rain in Seattle next week?"
        self.assertEqual(classify_market(market), "Other")


if __name__ == "__main__":
    unittest.main()
