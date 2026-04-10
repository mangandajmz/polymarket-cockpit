import unittest

import force_resolve as fr


class ForceResolveTests(unittest.TestCase):
    def test_build_pending_positions_keeps_traders_isolated(self):
        rows = [
            {
                "trader": "alice",
                "market": "Market A",
                "status": "PENDING",
                "our_size_usdc": "10",
                "copy_shares": "20",
                "condition_id": "cond-1",
                "outcome_index": "0",
                "position_id": "alice|cond-1|0",
            },
            {
                "trader": "bob",
                "market": "Market A",
                "status": "PENDING",
                "our_size_usdc": "12",
                "copy_shares": "24",
                "condition_id": "cond-1",
                "outcome_index": "0",
                "position_id": "bob|cond-1|0",
            },
        ]

        positions = fr.build_pending_positions(rows)

        self.assertEqual(len(positions), 2)
        self.assertIn(("alice|cond-1|0", "cond-1", 0), positions)
        self.assertIn(("bob|cond-1|0", "cond-1", 0), positions)
        self.assertEqual(positions[("alice|cond-1|0", "cond-1", 0)]["total_cost"], 10.0)
        self.assertEqual(positions[("bob|cond-1|0", "cond-1", 0)]["total_cost"], 12.0)


if __name__ == "__main__":
    unittest.main()
