# =============================================================
#   TRADER — Handles all Binance order execution
# =============================================================

import math
import time
import uuid
from binance.client import Client
from binance.exceptions import BinanceAPIException


# The full exchangeInfo payload lists every pair (~17 MB) and can take over
# 90 seconds to download. client.get_symbol_info() fetches all of it for each
# lookup, so the bot froze while building a Trader per wallet asset. Fetch
# only the requested pair (~5 KB) and cache it; filters change rarely.
SYMBOL_RULES_TTL_SEC = 3600
_symbol_rules_cache = {}   # (id(client), symbol) -> (loaded_at, info)
_listed_symbols_cache = {"client_id": None, "loaded_at": 0.0, "symbols": {}}


def get_symbol_rules(client, symbol: str) -> dict:
    """Return one pair's exchangeInfo entry, or raise ValueError if unlisted."""
    symbol = symbol.upper()
    key = (id(client), symbol)
    cached = _symbol_rules_cache.get(key)
    if cached and (time.time() - cached[0]) < SYMBOL_RULES_TTL_SEC:
        return cached[1]
    try:
        response = client._get("exchangeInfo", data={"symbol": symbol})
    except BinanceAPIException as exc:
        if exc.code == -1121:
            raise ValueError(f"{symbol} is not listed on Binance") from exc
        raise
    matches = [s for s in response.get("symbols", []) if s.get("symbol") == symbol]
    if not matches:
        raise ValueError(f"{symbol} is not listed on Binance")
    _symbol_rules_cache[key] = (time.time(), matches[0])
    return matches[0]


def load_listed_symbols(client, max_age_sec: float = SYMBOL_RULES_TTL_SEC) -> dict:
    """Return {pair: last price} for every listed pair from the small all-prices endpoint."""
    cache = _listed_symbols_cache
    fresh = (time.time() - cache["loaded_at"]) < max_age_sec
    if fresh and cache["client_id"] == id(client) and cache["symbols"]:
        return cache["symbols"]
    cache["symbols"] = {t["symbol"]: float(t["price"]) for t in client.get_all_tickers()}
    cache["client_id"] = id(client)
    cache["loaded_at"] = time.time()
    return cache["symbols"]


class Trader:
    def __init__(self, client: Client, symbol: str):
        self.client = client
        self.symbol = symbol
        self._load_symbol_filters()

    # ── Symbol Info ───────────────────────────────────────────
    def _load_symbol_filters(self):
        """Load LOT_SIZE, PRICE_FILTER and MIN_NOTIONAL filters from Binance."""
        info = get_symbol_rules(self.client, self.symbol)
        self.step_size    = 1.0
        self.min_qty      = 0.0
        self.min_notional = 10.0
        self.tick_size    = 0.00000001

        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                self.step_size = float(f["stepSize"])
                self.min_qty   = float(f["minQty"])
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                self.min_notional = float(f.get("minNotional", f.get("minNotional", 10)))
            if f["filterType"] == "PRICE_FILTER":
                self.tick_size = float(f["tickSize"])

    def _round_qty(self, qty: float) -> float:
        """Floor qty to the allowed step size precision."""
        if self.step_size == 0:
            return qty
        step_str = f"{self.step_size:.8f}".rstrip('0')
        precision = len(step_str.split('.')[1]) if '.' in step_str else 0
        rounded = math.floor(qty / self.step_size) * self.step_size
        return round(rounded, precision)

    def _round_price(self, price: float) -> float:
        """Round price to the allowed tick size precision."""
        if self.tick_size == 0:
            return price
        tick_str = f"{self.tick_size:.8f}".rstrip('0')
        precision = len(tick_str.split('.')[1]) if '.' in tick_str else 0
        rounded = math.floor(price / self.tick_size) * self.tick_size
        return round(rounded, precision)


    # ── Price & Balance ───────────────────────────────────────
    def get_current_price(self) -> float:
        """Fetch live market price for the symbol."""
        ticker = self.client.get_symbol_ticker(symbol=self.symbol)
        return float(ticker["price"])

    def get_balance(self, asset: str) -> float:
        """Return free (spendable) balance for a given asset."""
        account = self.client.get_account()
        for b in account["balances"]:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    # ── Orders ───────────────────────────────────────────────
    def buy_market(self, usdt_amount: float):
        """
        Place a market BUY order using a USDT amount.

        Returns:
            order (dict)       : Binance order response
            entry_price (float): Estimated fill price
            quantity (float)   : Quantity purchased
        """
        price    = self.get_current_price()
        qty      = self._round_qty(usdt_amount / price)

        if qty < self.min_qty:
            raise ValueError(
                f"Calculated quantity {qty} is below Binance minimum {self.min_qty}. "
                f"Increase your USDT trade amount."
            )

        notional = qty * price
        if notional < self.min_notional:
            raise ValueError(
                f"Order notional {notional:.2f} USDT is below minimum {self.min_notional} USDT."
            )

        order = self.client.create_order(
            symbol   = self.symbol,
            side     = Client.SIDE_BUY,
            type     = Client.ORDER_TYPE_MARKET,
            quantity = qty,
        )

        # Use actual weighted-average fill price from the order response,
        # not the pre-order snapshot. Slippage on market orders makes the
        # snapshot price unreliable for OCO level calculation.
        exec_qty   = float(order.get("executedQty", qty))
        cum_quote  = float(order.get("cummulativeQuoteQty", 0))
        if exec_qty > 0 and cum_quote > 0:
            fill_price = cum_quote / exec_qty
        else:
            fill_price = price  # fallback to snapshot if response is incomplete

        return order, fill_price, exec_qty

    def _format_price(self, price: float) -> str:
        rounded = self._round_price(price)
        if self.tick_size <= 0:
            return f"{rounded:.8f}"
        precision = max(0, int(round(-math.log(self.tick_size, 10), 0)))
        return f"{rounded:.{precision}f}"

    def buy_limit(self, usdt_amount: float, client_order_id: str | None = None):
        """
        Rest a limit buy on the best bid so the order is a maker, not a taker.
        Returns the open order, the limit price, and the quantity.
        The caller waits for the fill and cancels it if price runs away.
        """
        book = self.client.get_order_book(symbol=self.symbol, limit=5)
        if not book.get("bids"):
            raise ValueError(f"No bid on {self.symbol}")

        price_str = self._format_price(float(book["bids"][0][0]))
        limit_price = float(price_str)
        qty = self._round_qty(usdt_amount / limit_price)

        if qty < self.min_qty:
            raise ValueError(
                f"Calculated quantity {qty} is below Binance minimum {self.min_qty}. "
                f"Increase your USDT trade amount."
            )

        notional = qty * limit_price
        if notional < self.min_notional:
            raise ValueError(
                f"Order notional {notional:.2f} USDT is below minimum {self.min_notional} USDT."
            )

        order_params = {
            "symbol": self.symbol,
            "side": Client.SIDE_BUY,
            "type": Client.ORDER_TYPE_LIMIT,
            "timeInForce": Client.TIME_IN_FORCE_GTC,
            "quantity": qty,
            "price": price_str,
        }
        if client_order_id:
            order_params["newClientOrderId"] = client_order_id
        order = self.client.create_order(
            **order_params,
        )
        return order, limit_price, qty

    @staticmethod
    def new_client_order_id() -> str:
        """Create a Binance-valid id to persist before submitting an order."""
        return f"botbuy-{uuid.uuid4().hex[:24]}"

    def get_order_by_client_order_id(self, client_order_id: str):
        """Look up a submitted order when the submit response was lost."""
        return self.client.get_order(
            symbol=self.symbol, origClientOrderId=client_order_id
        )

    def cancel_and_confirm_terminal(self, order_id):
        """
        Cancel an outstanding order and return its confirmed terminal state.

        A cancellation request alone is not enough: an order may continue to
        fill while the request races through the exchange.  Callers must keep
        their persisted pending-order record when this method raises.
        """
        terminal_statuses = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
        cancel_error = None
        try:
            self.client.cancel_order(symbol=self.symbol, orderId=order_id)
        except Exception as exc:
            cancel_error = exc

        try:
            status = self.client.get_order(symbol=self.symbol, orderId=order_id)
        except Exception as status_error:
            raise RuntimeError(
                f"Cannot confirm terminal status after cancel attempt: {status_error}"
            ) from status_error

        if status.get("status") not in terminal_statuses:
            detail = f"cancel error: {cancel_error}" if cancel_error else "cancel not terminal"
            raise RuntimeError(
                f"Order {order_id} remains {status.get('status')} after cancel attempt ({detail})"
            )
        return status

    def reconcile_pending_buy(
        self, order_id=None, client_order_id=None, max_attempts: int = 3
    ):
        """
        Resolve an uncertain limit buy to a *terminal* exchange state.

        The caller must retain its pending intent if this raises.  Retrying is
        deliberately bounded: it is a recovery operation, not permission to
        continue placing orders while Binance's state is unknown.
        """
        if not order_id and not client_order_id:
            raise ValueError("pending buy has neither order_id nor client_order_id")

        last_error = None
        for attempt in range(max_attempts):
            try:
                if order_id:
                    status = self.client.get_order(
                        symbol=self.symbol, orderId=order_id
                    )
                else:
                    status = self.get_order_by_client_order_id(client_order_id)
                    order_id = status["orderId"]

                if status.get("status") in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                    return status
                return self.cancel_and_confirm_terminal(order_id)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < max_attempts:
                    continue
        raise RuntimeError(
            f"Pending buy could not be reconciled after {max_attempts} attempts: {last_error}"
        ) from last_error

    def sell_all(self, asset: str):
        """
        Sell the entire free balance of an asset.

        Returns:
            order (dict): Binance order response
        """
        balance = self.get_balance(asset)
        qty     = self._round_qty(balance)

        if qty <= 0 or qty < self.min_qty:
            raise ValueError(
                f"Sell failed: free balance is {balance} {asset}, "
                f"minimum sellable is {self.min_qty}."
            )

        order = self.client.create_order(
            symbol   = self.symbol,
            side     = Client.SIDE_SELL,
            type     = Client.ORDER_TYPE_MARKET,
            quantity = qty,
        )
        return order

    def place_oco_order(self, quantity: float, take_profit_price: float, stop_loss_price: float):
        """
        Place a Sell OCO order using the new Binance API v3 format.
        Above order = LIMIT_MAKER (Take Profit)
        Below order = STOP_LOSS (Stop Loss)
        Uses direct _request_api call to POST /api/v3/orderList/oco
        """
        qty = self._round_qty(quantity)
        tp_price = self._round_price(take_profit_price)
        sl_price = self._round_price(stop_loss_price)

        precision = max(0, int(round(-math.log(self.tick_size, 10), 0)))
        tp_price_str = f"{tp_price:.{precision}f}"
        sl_price_str = f"{sl_price:.{precision}f}"

        params = {
            "symbol":         self.symbol,
            "side":           "SELL",
            "quantity":       str(qty),
            "aboveType":      "LIMIT_MAKER",
            "abovePrice":     tp_price_str,
            "belowType":      "STOP_LOSS",
            "belowStopPrice": sl_price_str,
        }
        # Use direct API call since auto-generated wrapper may not exist
        return self.client._request_api("post", "orderList/oco", signed=True, data=params, version="v3")

