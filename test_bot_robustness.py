import csv
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

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

    def test_refresh_budget_resets_utc_daily_counters(self):
        bot = botmod.PaperBot()
        bot.daily_losses = 12.5
        bot.daily_wins = 4.0
        bot.daily_losses_per_trader = {"alice": 2}
        bot.daily_deploy_per_trader = {"alice": 18.0}
        bot._budget_date = date(2026, 4, 3)

        with patch.object(botmod, "utc_today", return_value=date(2026, 4, 4)):
            bot._refresh_budget()

        self.assertEqual(bot.daily_losses, 0.0)
        self.assertEqual(bot.daily_wins, 0.0)
        self.assertEqual(bot.daily_losses_per_trader, {})
        self.assertEqual(bot.daily_deploy_per_trader, {})
        self.assertEqual(bot._budget_date, date(2026, 4, 4))

    def test_runtime_snapshot_roundtrip_restores_store_state(self):
        bot = botmod.PaperBot()
        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))
        bot.closed_pnl = 7.25
        bot.wins = 1
        bot.losses = 0
        bot.daily_wins = 7.25
        bot.daily_losses = 0.0
        bot.daily_losses_per_trader = {"alice": 0}
        bot.daily_deploy_per_trader = {"alice": 10.0}
        bot.trader_stats = {"alice": {"wins": 1, "losses": 0}}
        pos = bot.positions[("alice", "cond-1", 0)]
        pos["status"] = "WIN"
        pos["pnl"] = 7.25

        botmod.persist_runtime_snapshot(bot)

        restored = botmod.PaperBot()
        loaded = botmod.load_positions_from_store(restored)

        self.assertTrue(loaded)
        self.assertEqual(restored.closed_pnl, 7.25)
        self.assertEqual(restored.wins, 1)
        self.assertEqual(restored.losses, 0)
        self.assertEqual(restored.trader_stats["alice"]["wins"], 1)
        self.assertEqual(restored.daily_deploy_per_trader["alice"], 10.0)
        self.assertIn(("alice", "cond-1", 0), restored.positions)
        restored_pos = restored.positions[("alice", "cond-1", 0)]
        self.assertEqual(restored_pos["position_id"], "alice|cond-1|0")
        self.assertEqual(restored_pos["status"], "WIN")
        self.assertEqual(len(restored.trade_log), 1)

    def test_resolve_position_snapshot_marks_win_and_updates_trade_log(self):
        bot = botmod.PaperBot()
        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))

        key = ("alice", "cond-1", 0)
        pos = bot.positions[key]
        botmod.resolve_position_snapshot(bot, key, px=1.0, resolved=True, now_ts=pos["opened_at"] + 3600)

        self.assertEqual(pos["status"], "WIN")
        self.assertGreater(bot.closed_pnl, 0)
        self.assertEqual(bot.wins, 1)
        self.assertEqual(bot.losses, 0)
        self.assertEqual(bot.trader_stats["alice"]["wins"], 1)
        self.assertEqual(bot.trade_log[0]["status"], "WIN")
        self.assertTrue(bot.trade_log[0]["resolved_pnl"].startswith("+"))

    def test_resolve_position_snapshot_force_closes_zero_price_loss(self):
        bot = botmod.PaperBot()
        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))

        key = ("alice", "cond-1", 0)
        pos = bot.positions[key]
        now_ts = pos["opened_at"] + ((botmod.ZERO_PRICE_CLOSE_HOURS + 1) * 3600)
        botmod.resolve_position_snapshot(bot, key, px=0.0, resolved=False, now_ts=now_ts)

        self.assertEqual(pos["status"], "LOSS")
        self.assertLess(bot.closed_pnl, 0)
        self.assertEqual(bot.losses, 1)
        self.assertEqual(bot.daily_losses_per_trader["alice"], 1)
        self.assertEqual(bot.trade_log[0]["status"], "LOSS")
        self.assertTrue(bot.trade_log[0]["resolved_pnl"].startswith("-"))

    def test_resolve_position_snapshot_force_closes_max_age(self):
        bot = botmod.PaperBot()
        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))

        key = ("alice", "cond-1", 0)
        pos = bot.positions[key]
        now_ts = pos["opened_at"] + ((botmod.MAX_OPEN_HOURS + 2) * 3600)
        botmod.resolve_position_snapshot(bot, key, px=0.40, resolved=False, now_ts=now_ts)

        self.assertEqual(pos["status"], "LOSS")
        self.assertIn("[MAX-AGE]", bot.status_msg)
        self.assertEqual(bot.losses, 1)
        self.assertEqual(bot.trade_log[0]["status"], "LOSS")

    def test_resolve_position_snapshot_resolved_with_missing_price_forces_zero_loss(self):
        bot = botmod.PaperBot()
        botmod.process_trade(bot, "alice", self._trade("tx-1", "Match A Winner"))

        key = ("alice", "cond-1", 0)
        botmod.resolve_position_snapshot(bot, key, px=None, resolved=True)

        pos = bot.positions[key]
        self.assertEqual(pos["status"], "LOSS")
        self.assertEqual(pos["last_price"], 0.0)
        self.assertLess(bot.closed_pnl, 0)
        self.assertEqual(bot.trade_log[0]["status"], "LOSS")

    def test_backfill_resolved_positions_from_csv_restores_closed_history(self):
        rows = [
            {
                "timestamp": "2026-04-03 00:00:00",
                "trader": "beachboy4",
                "market": "Legacy Market",
                "outcome": "YES",
                "whale_side": "BUY",
                "whale_size_usdc": "1000",
                "our_size_usdc": "10",
                "price": "0.5",
                "copy_shares": "20",
                "conviction": "1.0",
                "status": "WIN",
                "resolved_pnl": "+5.0000",
                "condition_id": "legacy-cond",
                "outcome_index": "0",
                "event_id": "legacy-e1",
                "position_id": "beachboy4|legacy-cond|0",
            },
            {
                "timestamp": "2026-04-03 00:02:00",
                "trader": "beachboy4",
                "market": "Legacy Market",
                "outcome": "YES",
                "whale_side": "BUY",
                "whale_size_usdc": "1200",
                "our_size_usdc": "5",
                "price": "0.25",
                "copy_shares": "20",
                "conviction": "1.2",
                "status": "WIN",
                "resolved_pnl": "+5.0000",
                "condition_id": "legacy-cond",
                "outcome_index": "0",
                "event_id": "legacy-e2",
                "position_id": "beachboy4|legacy-cond|0",
            },
        ]
        with open(botmod.CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=botmod.CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        bot = botmod.PaperBot()
        bot.trader_stats = {"beachboy4": {"wins": 1, "losses": 0}}

        botmod.backfill_resolved_positions_from_csv(bot)

        key = ("beachboy4", "legacy-cond", 0)
        self.assertIn(key, bot.positions)
        pos = bot.positions[key]
        self.assertEqual(pos["status"], "WIN")
        self.assertAlmostEqual(pos["total_cost"], 15.0)
        self.assertAlmostEqual(pos["total_shares"], 40.0)
        self.assertAlmostEqual(pos["pnl"], 5.0)


if __name__ == "__main__":
    unittest.main()
