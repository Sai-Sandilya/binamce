# =============================================================
#   RISK MANAGER — Tracks P&L and triggers Stop Loss / Take Profit
# =============================================================

class RiskManager:
    def __init__(self, entry_price: float, stop_loss_pct: float, take_profit_pct: float):
        """
        Args:
            entry_price      : Price at which the position was opened
            stop_loss_pct    : Negative value, e.g. -2.0 means stop at -2% loss
            take_profit_pct  : Positive value, e.g. 10.0 means stop at +10% gain
        """
        self.entry_price     = entry_price
        self.stop_loss_pct   = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        # Pre-calculate exact price levels for quick comparison
        self.stop_loss_price   = entry_price * (1 + stop_loss_pct / 100)
        self.take_profit_price = entry_price * (1 + take_profit_pct / 100)

    def get_pnl_pct(self, current_price: float) -> float:
        """Returns current P&L as a percentage. Negative = loss."""
        return ((current_price - self.entry_price) / self.entry_price) * 100

    def get_pnl_usdt(self, current_price: float, quantity: float) -> float:
        """Returns current P&L in USDT terms."""
        return (current_price - self.entry_price) * quantity

    def check(self, current_price: float) -> str:
        """
        Evaluate current price against thresholds.

        Returns:
            'STOP_LOSS'   — price hit stop loss level → exit trade
            'TAKE_PROFIT' — price hit take profit level → exit trade
            'CONTINUE'    — within safe range → keep holding
        """
        pnl = self.get_pnl_pct(current_price)

        if pnl <= self.stop_loss_pct:
            return 'STOP_LOSS'
        elif pnl >= self.take_profit_pct:
            return 'TAKE_PROFIT'
        return 'CONTINUE'

    def summary(self, current_price: float) -> dict:
        """Returns a full status snapshot."""
        pnl = self.get_pnl_pct(current_price)
        return {
            "entry_price"      : self.entry_price,
            "current_price"    : current_price,
            "pnl_pct"          : round(pnl, 4),
            "stop_loss_price"  : round(self.stop_loss_price, 6),
            "take_profit_price": round(self.take_profit_price, 6),
            "status"           : self.check(current_price),
        }
