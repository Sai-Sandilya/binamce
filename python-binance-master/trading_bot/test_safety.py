"""Focused regression tests for safety helpers.

Run directly with:
    python test_safety.py
"""

import os
import tempfile
import unittest

from state_store import load_state, save_state
from strategy import calculate_rsi, check_order_book, check_rsi_divergence


class BrokenOrderBookClient:
    def get_order_book(self, **_kwargs):
        raise RuntimeError("network down")


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

    def test_order_book_failure_blocks_entry(self):
        bullish, ratio, spread = check_order_book(BrokenOrderBookClient(), "BTCUSDT")
        self.assertFalse(bullish)
        self.assertEqual(ratio, 0.0)
        self.assertGreater(spread, 1.0)

    def test_rsi_needs_period_plus_one_closes(self):
        self.assertIsNone(calculate_rsi([100.0] * 14, 14))
        self.assertIsNotNone(calculate_rsi([100.0] * 15, 14))

    def test_divergence_rejects_insufficient_history(self):
        divergence, reason = check_rsi_divergence([100.0] * 20, 14, 20)
        self.assertFalse(divergence)
        self.assertEqual(reason, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
