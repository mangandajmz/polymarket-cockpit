import csv
import gc
import json
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

    def test_store_only_opportunities_still_bootstrap_shadow_model(self):
        bot = botmod.PaperBot()
        bot.store.upsert_opportunity({
            "event_id": "opp-only-1",
            "observed_at_utc": "2026-04-05 00:00:00",
            "trader": "alice",
            "market": "Match A Winner",
            "outcome": "Team A",
            "whale_side": "BUY",
            "whale_size_usdc": 1500.0,
            "price": 0.55,
            "condition_id": "cond-1",
            "outcome_index": 0,
            "transaction_hash": "tx-opp-only",
            "source_timestamp": 0,
            "opportunity_age_sec": 10,
            "trader_resolved_count": 5,
            "trader_win_rate": 60.0,
            "daily_losses_for_trader": 0,
            "daily_deploy_for_trader": 0.0,
            "bankroll": 300.0,
            "deployed_cap_pct": 0.0,
            "open_positions_count": 0,
            "median_whale_size": 1200.0,
            "conviction": 1.2,
            "perf_mult": 1.0,
            "dynamic_max_bet": 20.0,
            "recommended_size": 12.0,
            "copied_size_usdc": None,
            "copy_shares": None,
            "position_id": None,
            "decision": "SKIP",
            "decision_reason": "test_only_opportunity",
            "is_crypto": 0,
            "is_spread": 0,
            "is_futures": 0,
            "price_capped": 0,
            "duplicate_game": 0,
            "base_game": "match a winner",
            "bayes_posterior_mean": 0.60,
            "bayes_lower_bound": 0.52,
            "shadow_model_score": 0.73,
            "shadow_model_decision": "TAKE",
            "hybrid_veto_threshold": 0.70,
            "hybrid_veto_decision": "NO_ACTION",
            "hybrid_veto_reason": "heuristic_not_copied",
            "resolution_status": "WIN",
            "resolved_pnl": None,
            "resolved_at_utc": "2026-04-05 01:00:00",
        })

        restored = botmod.PaperBot()
        loaded = botmod.load_positions_from_store(restored)

        self.assertFalse(loaded)
        self.assertEqual(restored.shadow_model.examples_seen, 1)
        self.assertIn("opp-only-1", restored.shadow_trained_events)

    def test_opportunities_log_skips_and_resolution_updates(self):
        bot = botmod.PaperBot()

        skipped = self._trade("tx-skip", "BTC to $200k?")
        copied = self._trade("tx-copy", "Match A Winner")
        skipped_event = botmod.make_trade_event_id("alice", skipped)
        copied_event = botmod.make_trade_event_id("alice", copied)

        botmod.process_trade(bot, "alice", skipped)
        botmod.process_trade(bot, "alice", copied)

        with bot.store._connect() as conn:
            rows = {
                row["event_id"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM opportunities ORDER BY observed_at_utc"
                ).fetchall()
            }
            recs = {
                row["event_id"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM recommendations ORDER BY created_at_utc"
                ).fetchall()
            }

        self.assertEqual(rows[skipped_event]["decision"], "SKIP")
        self.assertEqual(rows[skipped_event]["decision_reason"], "crypto_market")
        self.assertEqual(rows[skipped_event]["hybrid_veto_decision"], "NO_ACTION")
        self.assertEqual(rows[skipped_event]["hybrid_veto_reason"], "heuristic_not_copied")
        self.assertEqual(rows[copied_event]["decision"], "COPIED")
        self.assertEqual(rows[copied_event]["position_id"], "alice|cond-1|0")
        self.assertIsNotNone(rows[copied_event]["bayes_posterior_mean"])
        self.assertIsNotNone(rows[copied_event]["bayes_lower_bound"])
        self.assertIsNotNone(rows[copied_event]["shadow_model_score"])
        self.assertIn(rows[copied_event]["shadow_model_decision"], ("SKIP", "TAKE"))
        self.assertEqual(rows[copied_event]["hybrid_veto_threshold"], botmod.HYBRID_VETO_THRESHOLD)
        self.assertIn(rows[copied_event]["hybrid_veto_decision"], ("ALLOW", "VETO"))
        self.assertIn(rows[copied_event]["hybrid_veto_reason"], ("score_above_threshold", "score_below_threshold", "model_warmup"))

        self.assertEqual(recs[skipped_event]["status"], "AVOID")
        self.assertIn("crypto_market", json.loads(recs[skipped_event]["risk_flags_json"]))
        self.assertIn(recs[copied_event]["status"], ("RECOMMEND", "WATCH"))
        self.assertIn("Decision:", recs[copied_event]["memo"])
        self.assertIn("paper recommendation only", recs[copied_event]["memo"])
        copied_evidence = json.loads(recs[copied_event]["evidence_json"])
        self.assertEqual(copied_evidence["trader"], "alice")
        self.assertEqual(copied_evidence["market"], "Match A Winner")
        self.assertEqual(copied_evidence["decision"], "COPIED")

        key = ("alice", "cond-1", 0)
        pos = bot.positions[key]
        botmod.resolve_position_snapshot(bot, key, px=1.0, resolved=True, now_ts=pos["opened_at"] + 3600)

        with bot.store._connect() as conn:
            resolved_row = dict(
                conn.execute(
                    "SELECT * FROM opportunities WHERE event_id = ?",
                    (copied_event,),
                ).fetchone()
            )
            resolved_rec = dict(
                conn.execute(
                    "SELECT * FROM recommendations WHERE event_id = ?",
                    (copied_event,),
                ).fetchone()
            )

        self.assertEqual(resolved_row["resolution_status"], "WIN")
        self.assertGreater(float(resolved_row["resolved_pnl"]), 0.0)
        self.assertEqual(resolved_rec["resolution_status"], "WIN")
        self.assertGreater(float(resolved_rec["resolved_pnl"]), 0.0)

    def test_resolve_logged_opportunities_labels_skipped_trade(self):
        bot = botmod.PaperBot()
        skipped = self._trade("tx-skip-2", "Match C Winner")
        skipped_event = botmod.make_trade_event_id("alice", skipped)

        with patch.object(botmod, "MIN_WHALE_SIZE", 5000.0):
            botmod.process_trade(bot, "alice", skipped)

        with patch.object(botmod, "get_price_resolved", return_value=(1.0, True)):
            botmod.resolve_logged_opportunities(bot, limit=10)

        with bot.store._connect() as conn:
            resolved_row = dict(
                conn.execute(
                    "SELECT * FROM opportunities WHERE event_id = ?",
                    (skipped_event,),
                ).fetchone()
            )

        self.assertEqual(resolved_row["decision"], "SKIP")
        self.assertEqual(resolved_row["decision_reason"], "below_min_whale_size")
        self.assertEqual(resolved_row["hybrid_veto_decision"], "NO_ACTION")
        self.assertEqual(resolved_row["resolution_status"], "WIN")
        self.assertIsNone(resolved_row["resolved_pnl"])

    def test_backfill_hybrid_veto_labels_updates_historical_rows(self):
        bot = botmod.PaperBot()
        historical_rows = [
            {
                "event_id": "hist-copy",
                "observed_at_utc": "2026-04-05 00:00:00",
                "trader": "alice",
                "market": "Match A Winner",
                "outcome": "Team A",
                "whale_side": "BUY",
                "whale_size_usdc": 1500.0,
                "price": 0.55,
                "condition_id": "cond-1",
                "outcome_index": 0,
                "transaction_hash": "tx-hist-copy",
                "source_timestamp": 0,
                "opportunity_age_sec": 10,
                "trader_resolved_count": 20,
                "trader_win_rate": 70.0,
                "daily_losses_for_trader": 0,
                "daily_deploy_for_trader": 0.0,
                "bankroll": 300.0,
                "deployed_cap_pct": 0.0,
                "open_positions_count": 0,
                "median_whale_size": 1200.0,
                "conviction": 1.2,
                "perf_mult": 1.0,
                "dynamic_max_bet": 20.0,
                "recommended_size": 12.0,
                "copied_size_usdc": 12.0,
                "copy_shares": 20.0,
                "position_id": "alice|cond-1|0",
                "decision": "COPIED",
                "decision_reason": "copied",
                "is_crypto": 0,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
                "duplicate_game": 0,
                "base_game": "Match A Winner",
                "bayes_posterior_mean": 0.62,
                "bayes_lower_bound": 0.55,
                "shadow_model_score": 0.73,
                "shadow_model_decision": "TAKE",
                "resolution_status": None,
                "resolved_pnl": None,
                "resolved_at_utc": None,
            },
            {
                "event_id": "hist-skip",
                "observed_at_utc": "2026-04-05 00:01:00",
                "trader": "alice",
                "market": "BTC to $200k?",
                "outcome": "YES",
                "whale_side": "BUY",
                "whale_size_usdc": 1500.0,
                "price": 0.55,
                "condition_id": "cond-2",
                "outcome_index": 0,
                "transaction_hash": "tx-hist-skip",
                "source_timestamp": 0,
                "opportunity_age_sec": 10,
                "trader_resolved_count": 20,
                "trader_win_rate": 70.0,
                "daily_losses_for_trader": 0,
                "daily_deploy_for_trader": 0.0,
                "bankroll": 300.0,
                "deployed_cap_pct": 0.0,
                "open_positions_count": 0,
                "median_whale_size": 1200.0,
                "conviction": 1.2,
                "perf_mult": 1.0,
                "dynamic_max_bet": 20.0,
                "recommended_size": 12.0,
                "copied_size_usdc": None,
                "copy_shares": None,
                "position_id": None,
                "decision": "SKIP",
                "decision_reason": "crypto_market",
                "is_crypto": 1,
                "is_spread": 0,
                "is_futures": 0,
                "price_capped": 0,
                "duplicate_game": 0,
                "base_game": "BTC to $200k?",
                "bayes_posterior_mean": 0.62,
                "bayes_lower_bound": 0.55,
                "shadow_model_score": 0.81,
                "shadow_model_decision": "TAKE",
                "resolution_status": None,
                "resolved_pnl": None,
                "resolved_at_utc": None,
            },
        ]
        for row in historical_rows:
            bot.store.upsert_opportunity(row)

        updated = botmod.backfill_hybrid_veto_labels(bot, bot.store.load_runtime_state()["opportunities"])
        self.assertEqual(updated, 2)

        with bot.store._connect() as conn:
            copy_row = dict(conn.execute("SELECT * FROM opportunities WHERE event_id = 'hist-copy'").fetchone())
            skip_row = dict(conn.execute("SELECT * FROM opportunities WHERE event_id = 'hist-skip'").fetchone())

        self.assertEqual(copy_row["hybrid_veto_decision"], "ALLOW")
        self.assertEqual(copy_row["hybrid_veto_reason"], "score_above_threshold")
        self.assertEqual(skip_row["hybrid_veto_decision"], "NO_ACTION")
        self.assertEqual(skip_row["hybrid_veto_reason"], "heuristic_not_copied")

    def test_backfill_hybrid_veto_labels_relabels_rows_after_threshold_change(self):
        bot = botmod.PaperBot()
        bot.store.upsert_opportunity({
            "event_id": "hist-veto-threshold",
            "observed_at_utc": "2026-04-05 00:02:00",
            "trader": "alice",
            "market": "Match B Winner",
            "outcome": "Team B",
            "whale_side": "BUY",
            "whale_size_usdc": 1500.0,
            "price": 0.55,
            "condition_id": "cond-3",
            "outcome_index": 0,
            "transaction_hash": "tx-hist-veto-threshold",
            "source_timestamp": 0,
            "opportunity_age_sec": 10,
            "trader_resolved_count": 20,
            "trader_win_rate": 70.0,
            "daily_losses_for_trader": 0,
            "daily_deploy_for_trader": 0.0,
            "bankroll": 300.0,
            "deployed_cap_pct": 0.0,
            "open_positions_count": 0,
            "median_whale_size": 1200.0,
            "conviction": 1.2,
            "perf_mult": 1.0,
            "dynamic_max_bet": 20.0,
            "recommended_size": 12.0,
            "copied_size_usdc": 12.0,
            "copy_shares": 20.0,
            "position_id": "alice|cond-3|0",
            "decision": "COPIED",
            "decision_reason": "copied",
            "is_crypto": 0,
            "is_spread": 0,
            "is_futures": 0,
            "price_capped": 0,
            "duplicate_game": 0,
            "base_game": "Match B Winner",
            "bayes_posterior_mean": 0.62,
            "bayes_lower_bound": 0.55,
            "shadow_model_score": 0.68,
            "shadow_model_decision": "TAKE",
            "hybrid_veto_threshold": 0.70,
            "hybrid_veto_decision": "VETO",
            "hybrid_veto_reason": "score_below_threshold",
            "resolution_status": None,
            "resolved_pnl": None,
            "resolved_at_utc": None,
        })

        updated = botmod.backfill_hybrid_veto_labels(bot, bot.store.load_runtime_state()["opportunities"])
        self.assertEqual(updated, 1)

        with bot.store._connect() as conn:
            row = dict(conn.execute("SELECT * FROM opportunities WHERE event_id = 'hist-veto-threshold'").fetchone())

        self.assertEqual(row["hybrid_veto_threshold"], botmod.HYBRID_VETO_THRESHOLD)
        self.assertEqual(row["hybrid_veto_decision"], "ALLOW")
        self.assertEqual(row["hybrid_veto_reason"], "score_above_threshold")

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
        self.assertEqual(pos["close_reason"], "CSV-BACKFILL")

    def test_invariants_skip_csv_backfilled_positions(self):
        bot = botmod.PaperBot()
        bot.positions[("legacy", "cond-x", 0)] = {
            "position_id": "legacy|cond-x|0",
            "condition_id": "cond-x",
            "outcome_index": 0,
            "title": "Legacy Market",
            "outcome": "YES",
            "trader": "legacy",
            "opened_at": botmod.time.time(),
            "opened_at_utc": "2026-04-03 00:00:00",
            "total_cost": 100.0,
            "total_shares": 150.0,
            "status": "WIN",
            "pnl": 25.0,
            "last_price": 1.0,
            "close_reason": "CSV-BACKFILL",
        }
        bot.trader_stats = {"legacy": {"wins": 1, "losses": 0}}

        botmod._validate_runtime_invariants(bot)

        self.assertEqual(bot.store.get_value("invariant_issues", []), [])

    def test_rebuild_trader_stats_from_positions_uses_canonical_closed_history(self):
        bot = botmod.PaperBot()
        bot.positions[("beachboy4", "cond-a", 0)] = {
            "position_id": "beachboy4|cond-a|0",
            "condition_id": "cond-a",
            "outcome_index": 0,
            "title": "Legacy Win",
            "outcome": "YES",
            "trader": "beachboy4",
            "opened_at": botmod.time.time(),
            "opened_at_utc": "2026-04-03 00:00:00",
            "total_cost": 10.0,
            "total_shares": 20.0,
            "status": "WIN",
            "pnl": 5.0,
            "last_price": 1.0,
            "close_reason": "CSV-BACKFILL",
        }
        bot.positions[("beachboy4", "cond-b", 1)] = {
            "position_id": "beachboy4|cond-b|1",
            "condition_id": "cond-b",
            "outcome_index": 1,
            "title": "Legacy Loss",
            "outcome": "NO",
            "trader": "beachboy4",
            "opened_at": botmod.time.time(),
            "opened_at_utc": "2026-04-03 01:00:00",
            "total_cost": 12.0,
            "total_shares": 24.0,
            "status": "LOSS",
            "pnl": -12.0,
            "last_price": 0.0,
            "close_reason": "CSV-BACKFILL",
        }
        bot.trader_stats = {"beachboy4": {"wins": 99, "losses": 0}}

        botmod.rebuild_trader_stats_from_positions(bot)

        self.assertEqual(bot.trader_stats, {"beachboy4": {"wins": 1, "losses": 1}})
        restored = bot.store.load_runtime_state()
        self.assertEqual(restored["trader_stats"]["beachboy4"], {"wins": 1, "losses": 1})


if __name__ == "__main__":
    unittest.main()
