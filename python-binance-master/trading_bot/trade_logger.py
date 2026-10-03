import csv
import os
from datetime import datetime

# Keep previous gross-only logs intact; this format adds net fee estimates.
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trades_v2.csv")

HEADERS = [
    "date", "time", "symbol", "entry_price", "exit_price",
    "quantity", "gross_pnl_pct", "gross_pnl_usdt",
    "estimated_fees_usdt", "net_pnl_pct", "net_pnl_usdt", "reason",
    "rsi", "vol_ratio", "ob_ratio", "duration_min"
]


def log_trade(symbol, entry_price, exit_price, quantity,
              reason, rsi=None, vol_ratio=None, ob_ratio=None,
              entry_time=None, fee_rate=0.001):
    """
    Append one completed trade to trades.csv.
    Completely fail-safe — never raises, never blocks a trade.
    """
    try:
        gross_pnl_pct  = (exit_price - entry_price) / entry_price * 100
        gross_pnl_usdt = (exit_price - entry_price) * quantity
        # Binance reports the exact commission per fill. This lightweight CSV
        # does not retain fill payloads, so record the conservative taker
        # estimate explicitly rather than incorrectly presenting gross P&L.
        estimated_fees_usdt = (entry_price * quantity + exit_price * quantity) * fee_rate
        net_pnl_usdt = gross_pnl_usdt - estimated_fees_usdt
        net_pnl_pct = (net_pnl_usdt / (entry_price * quantity) * 100) if entry_price and quantity else 0.0

        now = datetime.now()
        duration_min = None
        if entry_time is not None:
            import time
            duration_min = round((time.time() - entry_time) / 60, 1)

        file_exists = os.path.isfile(TRADE_LOG_PATH)
        with open(TRADE_LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "date":         now.strftime("%Y-%m-%d"),
                "time":         now.strftime("%H:%M:%S"),
                "symbol":       symbol,
                "entry_price":  entry_price,
                "exit_price":   exit_price,
                "quantity":     quantity,
                "gross_pnl_pct": round(gross_pnl_pct, 4),
                "gross_pnl_usdt": round(gross_pnl_usdt, 4),
                "estimated_fees_usdt": round(estimated_fees_usdt, 4),
                "net_pnl_pct": round(net_pnl_pct, 4),
                "net_pnl_usdt": round(net_pnl_usdt, 4),
                "reason":       reason,
                "rsi":          round(rsi, 2) if rsi else "",
                "vol_ratio":    round(vol_ratio, 2) if vol_ratio else "",
                "ob_ratio":     round(ob_ratio, 2) if ob_ratio else "",
                "duration_min": duration_min if duration_min else "",
            })
    except Exception as exc:
        # Logging must not block an exit, but an invisible missing trade log
        # defeats post-trade review and daily reconciliation.
        print(f"[TRADE LOG ERROR] {exc}", flush=True)
