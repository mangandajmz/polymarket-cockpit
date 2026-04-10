import unittest

import dynamic_watchlist as dw


class _DummyCache:
    def __init__(self):
        self._last = "never"
        self._data = {}

    def update(self, entries: dict):
        self._data.update({k: {"address": v, "active": False} for k, v in entries.items()})

    def set_active_traders(self, active_names: set):
        for name in list(self._data):
            self._data[name]["active"] = name in active_names

    def set_last_successful_refresh(self, ts: str):
        self._last = ts

    def get_last_successful_refresh(self) -> str:
        return self._last

    def __len__(self):
        return len(self._data)


class _DummyStore:
    def __init__(self):
        self.values = {}

    def set_value(self, key, value):
        self.values[key] = value


class _DummyBot:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.trader_addrs = {}
        self.last_addr_refresh = 0
        self.store = _DummyStore()


class WatchlistHardeningTests(unittest.TestCase):
    def test_refresh_keeps_existing_list_when_too_few_traders_qualify(self):
        original_req = dw._req
        original_wr = dw._estimate_win_rate
        original_cache = dw.AddressCache
        try:
            dw.AddressCache = _DummyCache
            dw._req = lambda *args, **kwargs: [
                {"name": "alpha", "proxyWallet": "0x1111111111", "pnl": 1000},
                {"name": "beta", "proxyWallet": "0x2222222222", "pnl": 900},
                {"name": "gamma", "proxyWallet": "0x3333333333", "pnl": 800},
            ]
            dw._estimate_win_rate = lambda addr, sample=10: 70.0 if addr == "0x1111111111" else 10.0

            mgr = dw.WatchlistManager(top_n=2, min_wr=60.0, refresh_hours=6, log_fn=lambda *_: None)
            mgr._active = {"existing_a": "0xaaaa", "existing_b": "0xbbbb"}
            mgr._bot = _DummyBot()
            mgr._do_refresh_inner()

            self.assertEqual(mgr.get_active(), {"existing_a": "0xaaaa", "existing_b": "0xbbbb"})
            self.assertEqual(mgr._bot.trader_addrs, {})
            self.assertEqual(
                mgr._bot.store.values["watchlist_health"]["last_error"],
                "insufficient_qualified_traders",
            )
        finally:
            dw._req = original_req
            dw._estimate_win_rate = original_wr
            dw.AddressCache = original_cache

    def test_estimate_win_rate_uses_condition_specific_clob_prices(self):
        original_req = dw._req
        try:
            calls = []

            def fake_req(url, params=None, retries=3):
                calls.append((url, params))
                if url == dw._DATA_API + "/trades":
                    return [
                        {
                            "side": "BUY",
                            "usdcSize": 100.0,
                            "price": 0.40,
                            "conditionId": "cond-win",
                            "outcomeIndex": 0,
                            "timestamp": 2,
                        },
                        {
                            "side": "BUY",
                            "usdcSize": 100.0,
                            "price": 0.70,
                            "conditionId": "cond-loss",
                            "outcomeIndex": 0,
                            "timestamp": 1,
                        },
                    ]
                if url == f"{dw._CLOB_API}/markets/cond-win":
                    return {"tokens": [{"price": 1.0}, {"price": 0.0}]}
                if url == f"{dw._CLOB_API}/markets/cond-loss":
                    return {"tokens": [{"price": 0.20}, {"price": 0.80}]}
                raise AssertionError(f"Unexpected request: {url} {params}")

            dw._req = fake_req
            wr = dw._estimate_win_rate("0xabc", sample=2)

            self.assertEqual(wr, 50.0)
            self.assertIn((f"{dw._CLOB_API}/markets/cond-win", None), calls)
            self.assertIn((f"{dw._CLOB_API}/markets/cond-loss", None), calls)
        finally:
            dw._req = original_req


if __name__ == "__main__":
    unittest.main()
