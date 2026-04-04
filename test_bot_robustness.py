import csv
import gc
import tempfile
import unittest
from pathlib import Path

import paper_trading_bot as botmod


class BotRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.orig_csv = botmod.CSV_FILE
        self.orig_db = botmod.STATE_DB_FILE
        self.orig_seen = botmod.SEEN_HASHES_FILE
        self.orig_min_whale = botmod.MIN_WHALE_SIZE
        self.orig_max_entry = botmod.MAX_ENTRY_PRICE
        self.orig_base_bet = botmod.BASE_BET
        self.orig_max_bet = botmod.MAX_BET

        botmod.CSV_FILE = self.tmpdir / "paper_trades.csv"
        botmod.STATE_DB_FILE = self.tmpdir / "bot_state.db"
        botmod.SEEN_HASHES_FILE = str(self.tmpdir / "seen_hashes.json")
        botmod.MIN_WHALE_SIZE = 0.0
        botmod.MAX_ENTRY_PRICE = 0.99
        botmod.BASE_BET = 10.0
        botmod.MAX_BET = 30.0
        botmod.init_csv()

    def tearDown(self):
        botmod.CSV_FILE = self.orig_csv
        botmod.STATE_DB_FILE = self.orig_db
        botmod.SEEN_HASHES_FILE = self.orig_seen
        botmod.MIN_WHALE_SIZE = self.orig_min_whale
        botmod.MAX_ENTRY_PRICE = self.orig_max_entry
        botmod.BASE_BET = self.orig_base_bet
        botmod.MAX_BET = self.orig_max_bet
        gc.collect()
        try:
            self.tmp.cleanup()
        except PermissionError:
            pass

    def _trade(self, tx_hash: str, title: str) -> dict:
        return {
            "transactionHash": tx_hash,
            "side": "BUY",
            "usdcSize": 1500,
            "price": 0.55,
            "conditionId": "cond-1",
            "outcomeIndex": 0,
            "title": title,
            "outcome": "Team A",
            "timestamp": int(botmod.time.time()),
            "type": "TRADE",
        }

    def test_positions_are_isolated_per_trader(self):
        bot = botmod.PaperBot()

        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))
        botmod.process_trade(bot, "bob", self._trade("tx-2", "Match B Winner"))

        self.assertEqual(len(bot.positions), 2)
        self.assertIn(("alice", "cond-1", 0), bot.positions)
        self.assertIn(("bob", "cond-1", 0), bot.positions)

        alice_pos = bot.positions[("alice", "cond-1", 0)]
        bob_pos = bot.positions[("bob", "cond-1", 0)]
        self.assertNotEqual(alice_pos["position_id"], bob_pos["position_id"])

    def test_csv_status_updates_only_target_trader_position(self):
        rows = [
            {
                "timestamp": "2026-04-03 00:00:00",
                "trader": "alice",
                "market": "Market",
                "outcome": "YES",
                "whale_side": "BUY",
                "whale_size_usdc": "1000",
                "our_size_usdc": "10",
                "price": "0.5",
                "copy_shares": "20",
                "conviction": "1.0",
                "status": "PENDING",
                "resolved_pnl": "",
                "condition_id": "cond-1",
                "outcome_index": "0",
                "event_id": "e1",
                "position_id": "alice|cond-1|0",
            },
            {
                "timestamp": "2026-04-03 00:00:00",
                "trader": "bob",
                "market": "Market",
                "outcome": "YES",
                "whale_side": "BUY",
                "whale_size_usdc": "1000",
                "our_size_usdc": "10",
                "price": "0.5",
                "copy_shares": "20",
                "conviction": "1.0",
                "status": "PENDING",
                "resolved_pnl": "",
                "condition_id": "cond-1",
                "outcome_index": "0",
                "event_id": "e2",
                "position_id": "bob|cond-1|0",
            },
        ]
        with open(botmod.CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=botmod.CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        botmod.update_csv_status("alice", "cond-1", 0, "WIN", 5.0)

        with open(botmod.CSV_FILE, "r", newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))

        alice_row = next(r for r in data if r["trader"] == "alice")
        bob_row = next(r for r in data if r["trader"] == "bob")
        self.assertEqual(alice_row["status"], "WIN")
        self.assertEqual(alice_row["resolved_pnl"], "+5.0000")
        self.assertEqual(bob_row["status"], "PENDING")
        self.assertEqual(bob_row["resolved_pnl"], "")


if __name__ == "__main__":
    unittest.main()
