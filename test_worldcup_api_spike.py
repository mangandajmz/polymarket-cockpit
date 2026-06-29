import unittest
from unittest.mock import Mock

from worldcup_api_spike import (
    PolymarketWorldCupSpike,
    classify_market,
    parse_jsonish_list,
    summarize_spike,
)


class WorldCupApiSpikeTests(unittest.TestCase):
    def test_parse_jsonish_list_handles_gamma_encoded_fields(self):
        self.assertEqual(parse_jsonish_list('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(parse_jsonish_list(["A", "B"]), ["A", "B"])
        self.assertEqual(parse_jsonish_list("not json"), [])
        self.assertEqual(parse_jsonish_list(None), [])

    def test_classify_market_finds_world_cup_and_combo_candidates(self):
        market = {
            "question": "2026 FIFA World Cup: Brazil vs France Combo",
            "description": "Parlay legs: Brazil wins and over 2.5 goals",
            "slug": "world-cup-brazil-france-combo",
        }

        result = classify_market(market)

        self.assertTrue(result.is_world_cup_related)
        self.assertTrue(result.is_combo_candidate)

    def test_spike_collects_events_markets_tokens_and_sample_books(self):
        client = Mock()
        client.get_json.side_effect = [
            {
                "events": [
                    {
                        "id": "event-1",
                        "title": "2026 FIFA World Cup",
                        "slug": "2026-fifa-world-cup",
                    }
                ],
                "markets": [],
            },
            [
                {
                    "id": "market-1",
                    "question": "2026 FIFA World Cup Winner",
                    "conditionId": "cond-1",
                    "outcomes": '["Brazil", "France"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                },
                {
                    "id": "market-2",
                    "question": "Brazil vs France Combo",
                    "description": "2026 FIFA World Cup parlay: Brazil wins + over 2.5 goals",
                    "outcomes": '["Yes", "No"]',
                    "clobTokenIds": '["token-combo-yes", "token-combo-no"]',
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                },
            ],
            {
                "bids": [{"price": "0.42", "size": "100"}],
                "asks": [{"price": "0.46", "size": "90"}],
            },
            {
                "bids": [{"price": "0.15", "size": "25"}],
                "asks": [{"price": "0.19", "size": "20"}],
            },
        ]
        spike = PolymarketWorldCupSpike(client=client, sample_price_count=2)

        result = spike.run(query="2026 FIFA World Cup", limit=10)

        self.assertEqual(result.query, "2026 FIFA World Cup")
        self.assertEqual(len(result.events), 1)
        self.assertEqual(len(result.markets), 2)
        self.assertEqual(result.token_count, 4)
        self.assertEqual(len(result.sample_books), 2)
        self.assertEqual(result.sample_books[0].midpoint, 0.44)
        self.assertEqual(result.combo_candidate_count, 1)
        self.assertFalse(result.wallet_auth_used)

    def test_summarize_spike_keeps_executable_combo_edge_unknown(self):
        result = Mock(
            query="world cup",
            events=[Mock()],
            markets=[Mock(), Mock()],
            token_count=3,
            sample_books=[],
            combo_candidate_count=1,
            wallet_auth_used=False,
            unsigned_rfq_quote_supported=None,
        )

        summary = summarize_spike(result)

        self.assertEqual(summary["combo_candidate_count"], 1)
        self.assertEqual(summary["unsigned_rfq_quote_supported"], "unknown")
        self.assertFalse(summary["wallet_auth_used"])


if __name__ == "__main__":
    unittest.main()
