"""Focused regression tests for safety helpers.

Run directly with:
    python test_safety.py
"""

import os
import tempfile
import time
import unittest

from binance.exceptions import BinanceAPIException
from bot import BinanceCallDeadlineExceeded, call_binance_with_deadline
from state_store import load_state, save_state
from strategy import calculate_rsi, check_order_book, check_rsi_divergence
import trader as trader_module
from trader import Trader


class CountingExchangeInfoClient:
    LISTED = ("BTCUSDT", "ETHUSDT")

    def __init__(self):
        self.requests = []

    def get_exchange_info(self):
        raise AssertionError("the full ~17 MB exchangeInfo must never be downloaded")

    def _get(self, path, data=None):
        assert path == "exchangeInfo"
        symbol = data["symbol"]
        self.requests.append(symbol)
        if symbol not in self.LISTED:
            raise BinanceAPIException(None, 400, '{"code":-1121,"msg":"Invalid symbol."}')
        return {"symbols": [{"symbol": symbol, "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "NOTIONAL", "minNotional": "5"},
        ]}]}

    def get_all_tickers(self):
        return [{"symbol": s, "price": "1"} for s in self.LISTED]


class BrokenOrderBookClient:
    def get_order_book(self, **_kwargs):
        raise RuntimeError("network down")


class CancelFailureClient:
    def cancel_order(self, **_kwargs):
        raise RuntimeError("network timeout")

    def get_order(self, **_kwargs):
        return {"status": "PARTIALLY_FILLED", "executedQty": "1"}


class CancelSucceededStatusUnavailableClient:
    def cancel_order(self, **_kwargs):
        return {"status": "CANCELED"}

    def get_order(self, **_kwargs):
        raise RuntimeError("status endpoint unavailable")


class FinalFillDuringCancelClient:
    def __init__(
        self, initial_status="PARTIALLY_FILLED", final_status="CANCELED",
        executed_qty="2", quote_qty="20"
    ):
        self.initial_status = initial_status
        self.final_status = final_status
        self.executed_qty = executed_qty
        self.quote_qty = quote_qty
        self.cancelled = False
        self.calls = 0

    def cancel_order(self, **_kwargs):
        self.cancelled = True
        return {"status": "CANCELED"}

    def get_order(self, **kwargs):
        self.calls += 1
        status = self.final_status if self.cancelled else self.initial_status
        if "origClientOrderId" in kwargs:
            return {
                "orderId": 42, "status": status,
                "executedQty": self.executed_qty,
                "cummulativeQuoteQty": self.quote_qty,
            }
        return {
            "orderId": 42, "status": status,
            "executedQty": self.executed_qty,
            "cummulativeQuoteQty": self.quote_qty,
        }


class SafetyTests(unittest.TestCase):
    def test_state_round_trip_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = load_state(path)
            state["position"] = {"symbol": "BTCUSDT", "entry_price": 100.0}
            state["daily"]["pnl_pct"] = -1.25
            save_state(state, path)
            restored = load_state(path)
            self.assertEqual(restored["position"]["symbol"], "BTCUSDT")
            self.assertEqual(restored["daily"]["pnl_pct"], -1.25)

    def test_corrupt_state_does_not_reset_safety_limits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not valid json")
            state = load_state(path)
            self.assertIsNotNone(state["load_error"])
            with self.assertRaises(RuntimeError):
                save_state(state, path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{not valid json")

    def test_order_book_failure_blocks_entry(self):
        bullish, ratio, spread = check_order_book(BrokenOrderBookClient(), "BTCUSDT")
        self.assertFalse(bullish)
        self.assertEqual(ratio, 0.0)
        self.assertGreater(spread, 1.0)

    def test_timeout_does_not_accept_nonterminal_partial_order(self):
        trader = Trader.__new__(Trader)
        trader.client = CancelFailureClient()
        trader.symbol = "BTCUSDT"
        with self.assertRaisesRegex(RuntimeError, "remains PARTIALLY_FILLED"):
            trader.cancel_and_confirm_terminal(123)

    def test_cancellation_success_without_status_confirmation_is_uncertain(self):
        trader = Trader.__new__(Trader)
        trader.client = CancelSucceededStatusUnavailableClient()
        trader.symbol = "BTCUSDT"
        with self.assertRaisesRegex(RuntimeError, "Cannot confirm terminal status"):
            trader.cancel_and_confirm_terminal(123)

    def test_reconciliation_uses_final_fills_that_arrive_during_cancellation(self):
        client = FinalFillDuringCancelClient()
        trader = Trader.__new__(Trader)
        trader.client = client
        trader.symbol = "BTCUSDT"
        status = trader.reconcile_pending_buy(order_id=42)
        self.assertTrue(client.cancelled)
        self.assertEqual(status["status"], "CANCELED")
        self.assertEqual(status["executedQty"], "2")
        self.assertEqual(status["cummulativeQuoteQty"], "20")

    def test_completed_buy_is_accepted_without_a_cancellation_attempt(self):
        client = FinalFillDuringCancelClient(
            initial_status="FILLED", final_status="FILLED"
        )
        trader = Trader.__new__(Trader)
        trader.client = client
        trader.symbol = "BTCUSDT"
        status = trader.reconcile_pending_buy(order_id=42)
        self.assertFalse(client.cancelled)
        self.assertEqual(status["status"], "FILLED")

    def test_lost_submission_response_is_reconciled_by_client_order_id(self):
        client = FinalFillDuringCancelClient(final_status="FILLED")
        trader = Trader.__new__(Trader)
        trader.client = client
        trader.symbol = "BTCUSDT"
        status = trader.reconcile_pending_buy(client_order_id="botbuy-response-lost")
        self.assertEqual(status["orderId"], 42)
        self.assertEqual(status["executedQty"], "2")

    def test_restart_retains_pending_intent_until_terminal_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = load_state(path)
            state["pending_buy"] = {
                "symbol": "BTCUSDT",
                "client_order_id": "botbuy-persisted",
                "submitted": False,
            }
            save_state(state, path)
            restored = load_state(path)
            self.assertEqual(
                restored["pending_buy"]["client_order_id"], "botbuy-persisted"
            )
            self.assertFalse(restored["pending_buy"]["submitted"])

    def test_startup_deadline_interrupts_a_stalled_api_call(self):
        with self.assertRaises(BinanceCallDeadlineExceeded):
            def blocked_call():
                time.sleep(0.1)
            call_binance_with_deadline(blocked_call, 0.01)

    def test_traders_fetch_each_pair_once_never_full_exchange_info(self):
        trader_module._symbol_rules_cache.clear()
        client = CountingExchangeInfoClient()
        for _ in range(25):
            Trader(client, "BTCUSDT")
            Trader(client, "ETHUSDT")
        self.assertEqual(sorted(client.requests), ["BTCUSDT", "ETHUSDT"])

    def test_unlisted_pair_is_rejected(self):
        trader_module._symbol_rules_cache.clear()
        with self.assertRaisesRegex(ValueError, "not listed"):
            Trader(CountingExchangeInfoClient(), "DUSTUSDT")

    def test_listed_symbols_come_from_small_price_endpoint(self):
        trader_module._listed_symbols_cache.update({"loaded_at": 0.0})
        symbols = trader_module.load_listed_symbols(CountingExchangeInfoClient())
        self.assertEqual(symbols, {"BTCUSDT": 1.0, "ETHUSDT": 1.0})

    def test_rsi_needs_period_plus_one_closes(self):
        self.assertIsNone(calculate_rsi([100.0] * 14, 14))
        self.assertIsNotNone(calculate_rsi([100.0] * 15, 14))

    def test_divergence_rejects_insufficient_history(self):
        divergence, reason = check_rsi_divergence([100.0] * 20, 14, 20)
        self.assertFalse(divergence)
        self.assertEqual(reason, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
