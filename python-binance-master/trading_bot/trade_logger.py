import csv
import os
from datetime import datetime

TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trades.csv")

HEADERS = [
    "date", "time", "symbol", "entry_price", "exit_price",
    "quantity", "pnl_pct", "pnl_usdt", "reason",
    "rsi", "vol_ratio", "ob_ratio", "duration_min"
]


def log_trade(symbol, entry_price, exit_price, quantity,
              reason, rsi=None, vol_ratio=None, ob_ratio=None,
              entry_time=None):
    """
    Append one completed trade to trades.csv.
    Completely fail-safe — never raises, never blocks a trade.
    """
    try:
        pnl_pct  = round((exit_price - entry_price) / entry_price * 100, 4)
        pnl_usdt = round((exit_price - entry_price) * quantity, 4)

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
                "pnl_pct":      pnl_pct,
                "pnl_usdt":     pnl_usdt,
                "reason":       reason,
                "rsi":          round(rsi, 2) if rsi else "",
                "vol_ratio":    round(vol_ratio, 2) if vol_ratio else "",
                "ob_ratio":     round(ob_ratio, 2) if ob_ratio else "",
                "duration_min": duration_min if duration_min else "",
            })
    except Exception:
        pass  # never block trading due to logging failure
