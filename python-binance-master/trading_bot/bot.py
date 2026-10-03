# =============================================================
#   BOT — Main trading loop (Multi-Coin Version)
#   Scans the market -> Finds best deal -> Buys -> Monitors
# =============================================================

import sys
import time
import importlib
import requests
from datetime import datetime

from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
import screener
from strategy import get_signal
from risk_manager import RiskManager
from trader import Trader
from trade_logger import log_trade
from state_store import load_state, save_state

# -- ANSI Console Colors ---------------------------------------
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def log(msg: str, color: str = WHITE):
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        print(f"{color}[{ts}]  {msg}{RESET}", flush=True)
    except UnicodeEncodeError:
        clean_msg = (
            msg.replace("🟢", "[BUY]")
               .replace("⚠️", "[WARN]")
               .replace("⛔", "[STOP]")
               .replace("🚨", "[ALERT]")
               .replace("🔍", "[SCAN]")
               .replace("📟", "[INIT]")
               .replace("🏆", "[TOP]")
               .replace("🪙", "[COIN]")
               .replace("📉", "[DOWN]")
               .replace("📈", "[UP]")
               .replace("🚦", "[SIGNAL]")
        )
        try:
            print(f"{color}[{ts}]  {clean_msg}{RESET}", flush=True)
        except Exception:
            print(f"[{ts}]  {msg.encode('ascii', 'ignore').decode('ascii')}", flush=True)


def divider(char="-", color=DIM):
    print(f"{color}{char * 60}{RESET}", flush=True)


def send_telegram(message: str, silent: bool = False):
    token = getattr(config, "TELEGRAM_TOKEN", None)
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", None)
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if not resp.ok:
            log(f"⚠️ Telegram error {resp.status_code}: {resp.text[:100]}", RED)
    except Exception as e:
        log(f"⚠️ Telegram notification failed: {e}", RED)


def execute_exit(trader: Trader, base_asset: str, reason: str, entry_price: float):
    """
    Sell all of base_asset at market and report the actual fill P&L.
    Returns the actual fill price if the sell fully filled, otherwise None.
    Caller must only set in_position=False when this returns a fill price —
    otherwise the bot would scan for new trades while still holding the coin.
    """
    divider("=", BOLD)
    log(f">> EXIT TRIGGERED  ->  {reason}", BOLD)
    divider("=", BOLD)

    try:
        log(f"Placing MARKET SELL for all {base_asset}...", YELLOW)
        sell_order = trader.sell_all(base_asset)

        # Derive actual fill price from the order response
        exec_qty  = float(sell_order.get("executedQty", 0))
        cum_quote = float(sell_order.get("cummulativeQuoteQty", 0))
        if exec_qty <= 0 or sell_order.get("status") != "FILLED":
            # A submitted order is not proof that the position is closed.
            # Confirm its exchange status before clearing local state.
            confirmed = trader.client.get_order(
                symbol=trader.symbol, orderId=sell_order["orderId"]
            )
            if confirmed.get("status") != "FILLED":
                raise RuntimeError(
                    f"Sell order {sell_order['orderId']} is {confirmed.get('status')}, not FILLED"
                )
            exec_qty = float(confirmed.get("executedQty", 0))
            cum_quote = float(confirmed.get("cummulativeQuoteQty", 0))
            if exec_qty <= 0:
                raise RuntimeError(f"Sell order {sell_order['orderId']} has no executed quantity")
        if exec_qty > 0 and cum_quote > 0:
            actual_exit_price = cum_quote / exec_qty
        else:
            actual_exit_price = trader.get_current_price()  # last-resort fallback

        pnl_pct = ((actual_exit_price - entry_price) / entry_price) * 100

        log(f"[SELL FILLED]  |  Order ID: {sell_order['orderId']}", GREEN)
        log(f"   Entry Price : {entry_price:.6f} USDT", WHITE)
        log(f"   Exit Price  : {actual_exit_price:.6f} USDT", WHITE)

        if pnl_pct >= 0:
            log(f"   Final P&L   : +{pnl_pct:.2f}%  PROFIT", GREEN)
            pnl_text = f"+{pnl_pct:.2f}% PROFIT 🟢"
        else:
            log(f"   Final P&L   : {pnl_pct:.2f}%  LOSS", RED)
            pnl_text = f"{pnl_pct:.2f}% LOSS 🔴"

        time.sleep(2)
        final_usdt = trader.get_balance("USDT")
        log(f"USDT Balance : {final_usdt:.4f} USDT", CYAN)

        send_telegram(
            f"🔔 <b>TRADE CLOSED</b>\n\n"
            f"<b>Pair:</b> {base_asset}USDT\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Entry:</b> {entry_price:.6f}\n"
            f"<b>Exit:</b> {actual_exit_price:.6f}\n"
            f"<b>P&amp;L:</b> {pnl_text}\n"
            f"<b>Wallet:</b> {final_usdt:.4f} USDT"
        )

        divider("=", BOLD)
        log("[TRADE CLOSED — Returning to scan mode]", BOLD)
        return actual_exit_price

    except Exception as e:
        log(f"[SELL FAILED]: {e}", RED)
        log("!! Staying in position — will retry next cycle.", RED)
        send_telegram(
            f"🚨 <b>SELL FAILED — Still In Position</b>\n\n"
            f"<b>Pair:</b> {base_asset}USDT\n"
            f"<b>Error:</b> {e}\n"
            f"⚠️ Bot is retrying. If this persists, sell manually on Binance."
        )
        divider("=", BOLD)
        return None


def run_bot():
    divider("=", CYAN)
    print(f"{BOLD}{CYAN}")
    print("   +-------------------------------------------------+")
    print("   |        BINANCE MULTI-COIN SCREENER  v1.3       |")
    print("   |   Scanning Top Coins | Stop Loss | Profit       |")
    print("   +-------------------------------------------------+")
    print(f"{RESET}", flush=True)
    divider("=", CYAN)

    client = Client(config.API_KEY, config.API_SECRET, testnet=config.TESTNET)

    # Sync clock to avoid Timestamp -1021 error
    try:
        server_time = client.get_server_time()
        client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
    except Exception as e:
        log(f"⚠️ Could not sync time with Binance: {e}", RED)

    mode_label = f"{RED}[LIVE TRADING]{RESET}" if not config.TESTNET else f"{GREEN}[TESTNET MODE]{RESET}"
    log(f"Mode     : {mode_label}")

    if config.MULTI_COIN:
        log(f"Mode     : {BOLD}MULTI-COIN SCREENER{RESET} (Scanning top {config.SCREENER_LIMIT} pairs)", CYAN)
    else:
        log(f"Mode     : {BOLD}SINGLE COIN{RESET} ({config.SYMBOL})", CYAN)

    log("Strategy : Daily EMA50>200 + ADX + 4h Fib + RSI divergence + OBV", CYAN)
    log("Orders   : Limit buy at the bid. Stop is the wider of the swing low and 1 ATR.", CYAN)
    divider("-")

    active_symbol = config.SYMBOL
    trader        = None
    entry_price   = None
    quantity      = None
    risk_mgr      = None
    in_position   = False
    order_list_id = None
    tp_order_id   = None
    sl_order_id   = None
    base_asset    = ""
    # Signal metrics saved at entry — used when logging completed trade
    signal_rsi    = None
    signal_vol    = None
    signal_ob     = None

    # Throttle: only send Telegram P&L update every 5 minutes while in position
    last_tg_update   = 0
    TG_UPDATE_INTERVAL = 300  # seconds

    # Send startup notification once
    startup_notified = False

    # ── TRADE GUARDS ──────────────────────────────────────────
    sl_cooldown       = {}    # symbol -> timestamp when SL hit; block for 30 min
    entry_time        = None  # when current trade was entered
    MIN_HOLD_SEC      = 300          # 5 min — don't react to the first noisy minutes
    MAX_NO_PROFIT_SEC = 4 * 60 * 60  # one 4h candle; a 45-min exit was killing this setup
    peak_profit_ever  = 0.0   # highest P&L% seen in current trade

    # ── DAILY LIMITS ──────────────────────────────────────────
    from datetime import date
    daily_date        = date.today()
    daily_pnl_pct     = 0.0   # cumulative P&L% today
    daily_trades      = 0     # number of trades today
    daily_wins        = 0
    daily_losses      = 0
    MAX_DAILY_LOSS    = -3.0  # stop trading if daily P&L drops below -3%
    MAX_DAILY_TRADES  = 5     # max 5 trades per day
    daily_summary_sent = False  # send once per day around midnight

    # Persist daily circuit-breaker counters and a filled position before
    # attempting its OCO.  Binance remains authoritative for fills/orders.
    persistent_state = load_state()
    if persistent_state["date"] == daily_date.isoformat():
        persisted_daily = persistent_state["daily"]
        daily_pnl_pct = float(persisted_daily.get("pnl_pct", 0.0))
        daily_trades = int(persisted_daily.get("trades", 0))
        daily_wins = int(persisted_daily.get("wins", 0))
        daily_losses = int(persisted_daily.get("losses", 0))
    else:
        persistent_state["date"] = daily_date.isoformat()
        persistent_state["daily"] = {
            "pnl_pct": 0.0, "trades": 0, "wins": 0, "losses": 0,
        }
        save_state(persistent_state)

    def persist_runtime_state():
        persistent_state["date"] = daily_date.isoformat()
        persistent_state["daily"] = {
            "pnl_pct": daily_pnl_pct,
            "trades": daily_trades,
            "wins": daily_wins,
            "losses": daily_losses,
        }
        persistent_state["position"] = (
            {
                "symbol": active_symbol,
                "base_asset": base_asset,
                "entry_price": entry_price,
                "quantity": quantity,
                "stop_price": risk_mgr.stop_loss_price if risk_mgr else None,
                "take_profit_price": risk_mgr.take_profit_price if risk_mgr else None,
                "order_list_id": order_list_id,
                "tp_order_id": tp_order_id,
                "sl_order_id": sl_order_id,
                "entry_time": entry_time,
                "signal_rsi": signal_rsi,
                "signal_vol": signal_vol,
                "signal_ob": signal_ob,
            }
            if in_position else None
        )
        save_state(persistent_state)

    def net_pnl_pct(entry, exit_price):
        """Conservative fee-inclusive P&L used by daily loss limits."""
        gross = ((exit_price - entry) / entry) * 100
        fee_rate = float(getattr(config, "TAKER_FEE_RATE", 0.001))
        round_trip_fee = fee_rate * (1 + (exit_price / entry)) * 100
        return gross - round_trip_fee

    # ── SMART ALERTS ──────────────────────────────────────────
    last_heartbeat    = time.time()   # 3-hour alive ping
    HEARTBEAT_SEC     = 10800         # 3 hours
    tp_alert_sent     = False         # alert when 0.5% away from TP
    sl_alert_sent     = False         # alert when 0.5% away from SL

    # ── TRAILING PROFIT LOCK ──────────────────────────────────
    trail_sl_price    = None  # dynamically raised SL once profit > 0.5%

    # ── AUTO-RESTORE ACTIVE OCO POSITION ─────────────────────
    log("Checking for active open trades on Binance...", DIM)
    try:
        open_orders = client.get_open_orders()
        oco_orders = [o for o in open_orders if o.get('orderListId', -1) != -1]
        if oco_orders:
            first_oco = oco_orders[0]
            active_symbol = first_oco['symbol']
            order_list_id = first_oco['orderListId']
            active_oco_orders = [
                o for o in oco_orders
                if o['symbol'] == active_symbol and o.get('orderListId') == order_list_id
            ]

            base_asset = active_symbol.replace("USDT", "")
            trader     = Trader(client, active_symbol)
            quantity   = float(first_oco['origQty'])
            tp_order_id = None
            sl_order_id = None
            tp_price = None
            sl_price = None

            for o in active_oco_orders:
                if o['type'] == "LIMIT_MAKER":
                    tp_order_id = o['orderId']
                    tp_price = float(o['price'])
                elif o['type'] == "STOP_LOSS":
                    sl_order_id = o['orderId']
                    sl_price = float(o['stopPrice'])

            # Reconstruct entry price from trade history
            try:
                trades = client.get_my_trades(symbol=active_symbol, limit=100)
                buy_trades = [t for t in trades if t.get('isBuyer', False)]
                if buy_trades:
                    # Use the most recent buy order only; averaging historical
                    # buys produces the wrong recovery price after a restart.
                    latest_order_id = max(buy_trades, key=lambda t: t['time'])['orderId']
                    latest_buys = [t for t in buy_trades if t['orderId'] == latest_order_id]
                    total_qty   = sum(float(t['qty']) for t in latest_buys)
                    total_quote = sum(float(t['quoteQty']) for t in latest_buys)
                    entry_price = total_quote / total_qty if total_qty > 0 else float(latest_buys[-1]['price'])
                else:
                    entry_price = float(first_oco.get('stopPrice', 0)) / (1 + config.STOP_LOSS_PCT / 100)
            except Exception:
                entry_price = float(first_oco.get('stopPrice', 0)) / (1 + config.STOP_LOSS_PCT / 100)

            in_position      = True
            entry_time       = time.time()  # approximate — trade was open before restart
            peak_profit_ever = 0.0
            trail_sl_price   = None
            if tp_price is not None and sl_price is not None and entry_price > 0:
                restored_sl_pct = ((sl_price - entry_price) / entry_price) * 100
                restored_tp_pct = ((tp_price - entry_price) / entry_price) * 100
                risk_mgr = RiskManager(entry_price, restored_sl_pct, restored_tp_pct)
            else:
                # The exchange OCO remains active; these values only power
                # local monitoring if Binance omitted one of its order legs.
                risk_mgr = RiskManager(entry_price, config.STOP_LOSS_PCT, config.TAKE_PROFIT_PCT)
                log("⚠️ Could not reconstruct both OCO price levels; local monitor uses config fallback.", YELLOW)

            log(f"🔄 [RESTORED] Active position: {active_symbol}!", GREEN)
            log(f"   Entry Price  : {entry_price:.6f} USDT", WHITE)
            log(f"   Quantity     : {quantity} {base_asset}", WHITE)
            log(f"   Stop Loss @  : {risk_mgr.stop_loss_price:.6f}", RED)
            log(f"   Take Profit @: {risk_mgr.take_profit_price:.6f}", GREEN)
            log(f"   OCO List ID  : {order_list_id}", CYAN)
            divider("-")
            persist_runtime_state()
    except Exception as e:
        log(f"⚠️ Position auto-recovery check failed: {e}", DIM)

    # If the process crashed after a buy but before its OCO was recorded by
    # Binance, state is the only evidence of that holding. Do not silently
    # resume scanning around it. Recreate exchange-side protection; if that
    # cannot be done, sell rather than leaving an unprotected position.
    saved_position = persistent_state.get("position")
    if not in_position and saved_position:
        try:
            active_symbol = saved_position["symbol"]
            base_asset = saved_position["base_asset"]
            trader = Trader(client, active_symbol)
            held_qty = trader._round_qty(trader.get_balance(base_asset))
            if held_qty >= trader.min_qty:
                entry_price = float(saved_position["entry_price"])
                stop_price = float(saved_position["stop_price"])
                take_profit_price = float(saved_position["take_profit_price"])
                quantity = held_qty
                restored_sl_pct = ((stop_price - entry_price) / entry_price) * 100
                restored_tp_pct = ((take_profit_price - entry_price) / entry_price) * 100
                risk_mgr = RiskManager(entry_price, restored_sl_pct, restored_tp_pct)
                in_position = True
                entry_time = saved_position.get("entry_time") or time.time()
                signal_rsi = saved_position.get("signal_rsi")
                signal_vol = saved_position.get("signal_vol")
                signal_ob = saved_position.get("signal_ob")
                log(
                    f"🚨 Restored {active_symbol} without an OCO. Recreating protection now.",
                    RED,
                )
                oco = trader.place_oco_order(quantity, take_profit_price, stop_price)
                order_list_id = oco["orderListId"]
                for order in oco.get("orderReports") or oco.get("orders") or []:
                    if order.get("type") == "LIMIT_MAKER":
                        tp_order_id = order["orderId"]
                    elif order.get("type") in ("STOP_LOSS", "STOP_LOSS_LIMIT"):
                        sl_order_id = order["orderId"]
                persist_runtime_state()
            else:
                # State is stale: the position was closed outside the bot.
                persistent_state["position"] = None
                save_state(persistent_state)
                log("Saved position is no longer held; cleared stale state.", YELLOW)
        except Exception as restore_err:
            log(
                f"🚨 Could not recreate protection for saved position: {restore_err}. "
                "Attempting emergency close.",
                RED,
            )
            sold = (
                in_position and trader is not None
                and execute_exit(trader, base_asset, "RECOVERY_OCO_FAILED", entry_price)
            )
            if sold:
                in_position = False
                persistent_state["position"] = None
                save_state(persistent_state)
            else:
                send_telegram(
                    f"🚨 <b>UNPROTECTED POSITION</b>\n{saved_position.get('symbol', 'unknown')}\n"
                    f"Could not restore OCO: {restore_err}\nSell manually immediately."
                )

    # Do not open a fresh trade if Binance reports a meaningful asset that is
    # not represented by a bot position. It may be a previously interrupted
    # buy or a manual holding; either way, guessing its cost basis is unsafe.
    reconciliation_halt = False
    if not in_position:
        try:
            account = client.get_account()
            unknown_assets = []
            for balance in account.get("balances", []):
                asset = balance.get("asset", "")
                quantity_held = float(balance.get("free", 0) or 0) + float(balance.get("locked", 0) or 0)
                if asset in ("USDT", "BNB") or quantity_held <= 0:
                    continue
                symbol = f"{asset}USDT"
                try:
                    candidate = Trader(client, symbol)
                    if candidate._round_qty(quantity_held) >= candidate.min_qty:
                        unknown_assets.append(f"{quantity_held:g} {asset}")
                except Exception:
                    # No active USDT market: do not treat dust/delisted assets
                    # as an open bot position.
                    continue
            if unknown_assets:
                reconciliation_halt = True
                message = ", ".join(unknown_assets)
                log(f"🚨 Unreconciled Binance holdings: {message}. New entries are paused.", RED)
                send_telegram(
                    f"🚨 <b>UNRECONCILED HOLDINGS</b>\n{message}\n"
                    "Bot will not open new trades until this is resolved."
                )
        except Exception as reconciliation_err:
            reconciliation_halt = True
            log(f"🚨 Could not reconcile startup holdings: {reconciliation_err}. New entries are paused.", RED)

    while True:
        try:
            importlib.reload(config)
            importlib.reload(screener)

            # Send startup Telegram once per process start
            if not startup_notified:
                send_telegram(
                    f"📟 <b>Bot Started (v1.3)</b>\n\n"
                    f"<b>Mode:</b> {'LIVE' if not config.TESTNET else 'TESTNET'}\n"
                    f"<b>Strategy:</b> Daily EMA50&gt;EMA200 + 4h Fib golden zone + RSI divergence + volume\n"
                    f"<b>TP/SL:</b> swing high / beyond swing low\n"
                    f"<b>Scanning:</b> Top {config.SCREENER_LIMIT} coins\n\n"
                    f"✅ Telegram notifications are active."
                )
                startup_notified = True

            # ── DAILY RESET AT MIDNIGHT ───────────────────────
            today    = date.today()
            now_hour = datetime.now().hour

            # Send daily summary around 11:55 PM once
            if now_hour == 23 and not daily_summary_sent and daily_trades > 0:
                try:
                    temp_t   = Trader(client, "BNBUSDT")
                    wal      = temp_t.get_balance("USDT")
                    win_rate = int(daily_wins / daily_trades * 100) if daily_trades else 0
                    pnl_emoji = "🟢" if daily_pnl_pct >= 0 else "🔴"
                    send_telegram(
                        f"📊 <b>Daily Summary — {daily_date}</b>\n\n"
                        f"📈 Trades taken : {daily_trades}/{MAX_DAILY_TRADES}\n"
                        f"✅ Wins         : {daily_wins}\n"
                        f"❌ Losses       : {daily_losses}\n"
                        f"🎯 Win rate     : {win_rate}%\n"
                        f"{pnl_emoji} Total P&L     : {daily_pnl_pct:+.2f}%\n"
                        f"💰 Wallet       : {wal:.4f} USDT\n\n"
                        f"Bot continues scanning overnight..."
                    )
                    daily_summary_sent = True
                    log(f"Daily summary sent. Trades:{daily_trades} Wins:{daily_wins} P&L:{daily_pnl_pct:+.2f}%", CYAN)
                except Exception as e:
                    log(f"Daily summary failed: {e}", DIM)

            if today != daily_date:
                log(f"New day! Yesterday: {daily_trades} trades | Wins:{daily_wins} Losses:{daily_losses} | P&L:{daily_pnl_pct:+.2f}%", CYAN)
                daily_date         = today
                daily_pnl_pct      = 0.0
                daily_trades       = 0
                daily_wins         = 0
                daily_losses       = 0
                daily_summary_sent = False
                persist_runtime_state()

            # ── NO POSITION: Scan the market ──────────────────
            if not in_position:
                if reconciliation_halt:
                    log("[Safety Halt] Resolve unreconciled holdings before new entries.", RED)
                    time.sleep(60)
                    continue
                temp_trader  = Trader(client, "BNBUSDT")
                usdt_balance = temp_trader.get_balance("USDT")
                log(f"Wallet Balance: {BOLD}{usdt_balance:.4f} USDT{RESET} | Today: {daily_trades}/{MAX_DAILY_TRADES} trades | Daily P&L: {daily_pnl_pct:+.2f}%", CYAN)
                maker_fee, taker_fee, bnb_free = screener.read_trading_fee(client)
                # Use the worse taker rate for risk calculations. An entry
                # resting at the bid can still execute as a taker after the
                # order book changes, and a stop-loss is always a taker exit.
                config.TAKER_FEE_RATE = taker_fee
                est_fee = usdt_balance * (config.TRADE_AMOUNT_PCT / 100) * (taker_fee * 2)
                if bnb_free > 0:
                    log(
                        f"[Fees] Account rate — maker {maker_fee*100:.3f}% / taker {taker_fee*100:.3f}%. "
                        f"BNB free: {bnb_free:.4f}; confirm 'Use BNB to pay fees' is enabled in Binance. "
                        f"Conservative round-trip estimate: {est_fee:.4f} USDT.",
                        CYAN,
                    )
                elif bnb_free <= 0:
                    log(
                        f"[Fees] No free BNB. Fee is taken from the coin "
                        f"(maker {maker_fee*100:.3f}% / taker {taker_fee*100:.3f}%). "
                        f"Hold BNB and enable 'Use BNB to pay fees' in Binance.",
                        YELLOW,
                    )

                # Daily loss limit — stop trading if lost too much today
                if daily_pnl_pct <= MAX_DAILY_LOSS:
                    log(f"[Daily Limit] Daily loss {daily_pnl_pct:.2f}% hit limit {MAX_DAILY_LOSS}% — pausing until tomorrow", RED)
                    send_telegram(f"🛑 <b>Daily Loss Limit Hit</b>\nLost {daily_pnl_pct:.2f}% today. Bot paused until midnight.")
                    time.sleep(300)
                    continue

                # Max trades per day
                if daily_trades >= MAX_DAILY_TRADES:
                    log(f"[Daily Limit] Reached {daily_trades} trades today — pausing until tomorrow", YELLOW)
                    time.sleep(300)
                    continue

                if config.MULTI_COIN:
                    log("Scanning market for the best entry...", YELLOW)
                    symbols = screener.get_top_usdt_pairs(client, config.SCREENER_LIMIT)

                    # Time-of-day filter: avoid 00:00–07:00 UTC (dead market, bad signals)
                    utc_hour = time.gmtime().tm_hour
                    if 0 <= utc_hour < 7:
                        log(f"[Time Filter] Dead market hours (UTC {utc_hour:02d}:xx) — sleeping 10 min", YELLOW)
                        time.sleep(600)
                        continue

                    # Remove coins still in SL cooldown (2 hour block after SL hit)
                    now_ts = time.time()
                    cooled = {s: t for s, t in sl_cooldown.items() if now_ts - t < 7200}
                    sl_cooldown.clear(); sl_cooldown.update(cooled)
                    if sl_cooldown:
                        blocked = list(sl_cooldown.keys())
                        log(f"[Cooldown] Skipping {blocked} (SL hit <2h ago)", DIM)
                        symbols = [s for s in symbols if s not in sl_cooldown]

                    log(f"[Watchlist] {len(symbols)} coins, ranked by 24h USDT volume:", CYAN)
                    for i in range(0, len(symbols), 5):
                        row = "   ".join(
                            f"{n + 1:>2}. {sym}"
                            for n, sym in enumerate(symbols[i:i + 5], start=i)
                        )
                        print(f"   {row}", flush=True)

                    candidates = screener.find_best_entry(client, symbols, config.KLINE_INTERVAL)

                    if not candidates:
                        log("No BUY signal found. Retrying...", DIM)
                        time.sleep(30)
                        continue

                    best          = candidates[0]
                    active_symbol = best['symbol']
                    signal        = best['signal']
                    rsi           = best['rsi']
                    ema_f         = best['ema_f']
                    ema_s         = best['ema_s']
                    macd_hist     = best['macd_hist']
                    vol_ratio     = best['vol_ratio']
                    at_bb_lower   = best['at_bb_lower']
                    dyn_tp        = best['tp_pct']
                    dyn_sl        = best['sl_pct']
                    ob_ratio      = best.get('ob_ratio', 1.0)
                    reason        = best['reason']

                    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
                    print(f"   [Best Match] {BOLD}{active_symbol}{RESET} | "
                          f"{reason} | "
                          f"RSI: {rsi_txt} | "
                          f"Daily EMA50/200: {ema_f:.4f}/{ema_s:.4f} | "
                          f"Vol: {vol_ratio:.1f}x | "
                          f"TP:{dyn_tp}% SL:{dyn_sl}%",
                          flush=True)

                    if getattr(config, "TELEGRAM_SEND_SCANS", False):
                        tg_time = datetime.now().strftime("%H:%M:%S")
                        send_telegram(
                            f"🔍 <b>BUY Signal Found — {tg_time}</b>\n\n"
                            f"🏆 <b>Coin:</b> #{active_symbol}\n"
                            f"{reason}\n"
                            f"RSI: {rsi_txt} | Vol: {vol_ratio:.1f}x\n"
                            f"🎯 TP: +{dyn_tp}% | SL: -{dyn_sl}%\n"
                            f"💰 <b>Wallet:</b> {usdt_balance:.4f} USDT",
                            silent=True
                        )
                else:
                    active_symbol = config.SYMBOL
                    data = get_signal(
                        client, active_symbol, config.KLINE_INTERVAL,
                        rsi_period        = config.RSI_PERIOD,
                        ema_fast          = config.EMA_FAST,
                        ema_slow          = config.EMA_SLOW,
                        rsi_oversold      = config.RSI_OVERSOLD,
                        volume_multiplier = getattr(config, "VOLUME_MULTIPLIER", 1.2),
                        bb_period         = getattr(config, "BB_PERIOD", 20),
                    )
                    signal      = data['signal']
                    rsi         = data['rsi']
                    ema_f       = data['ema_f']
                    ema_s       = data['ema_s']
                    macd_hist   = data['macd_hist']
                    vol_ratio   = data['vol_ratio']
                    at_bb_lower = data['at_bb_lower']
                    dyn_tp      = data['tp_pct']
                    dyn_sl      = data['sl_pct']
                    ob_ratio    = data.get('ob_ratio', 1.0)
                    reason      = data['reason']

                    now     = datetime.now().strftime("%H:%M:%S")
                    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                    print(f"{DIM}[{now}]{RESET} {active_symbol} | RSI: {rsi_str} | "
                          f"Vol: {vol_ratio:.1f}x | Signal: {signal} | {reason}",
                          flush=True)

                    if getattr(config, "TELEGRAM_SEND_SCANS", False) and signal == "BUY":
                        send_telegram(
                            f"🔍 <b>BUY Signal — {now}</b>\n\n"
                            f"🪙 #{active_symbol}\n"
                            f"RSI: {rsi_str} | Vol: {vol_ratio:.1f}x\n"
                            f"TP: +{dyn_tp}% | SL: -{dyn_sl}%\n"
                            f"💰 {usdt_balance:.4f} USDT",
                            silent=True
                        )

                if signal == "BUY":
                    divider("-")
                    log(f"🟢 BUY SIGNAL on {active_symbol}! ({reason})", GREEN)

                    trader        = Trader(client, active_symbol)
                    usdt_balance  = trader.get_balance("USDT")
                    trade_amount  = round(usdt_balance * (config.TRADE_AMOUNT_PCT / 100), 4)
                    base_asset    = active_symbol.replace("USDT", "")

                    buy_ok = False
                    try:
                        order, limit_price, quantity = trader.buy_limit(trade_amount)
                        wait_sec = getattr(config, "LIMIT_FILL_SEC", 120)
                        log(
                            f"[LIMIT BUY] {active_symbol} @ {limit_price:.6f} "
                            f"qty {quantity} — waiting up to {wait_sec}s",
                            YELLOW,
                        )
                        deadline = time.time() + wait_sec
                        status = order
                        while time.time() < deadline:
                            status = client.get_order(symbol=active_symbol, orderId=order["orderId"])
                            if status.get("status") == "FILLED":
                                break
                            if status.get("status") in ("CANCELED", "REJECTED", "EXPIRED"):
                                raise RuntimeError(f"Limit buy {status.get('status')}")
                            time.sleep(3)
                        else:
                            try:
                                client.cancel_order(symbol=active_symbol, orderId=order["orderId"])
                            except Exception:
                                pass
                            status = client.get_order(symbol=active_symbol, orderId=order["orderId"])
                            if float(status.get("executedQty", 0) or 0) <= 0:
                                raise RuntimeError("Limit buy not filled — cancelled")
                            log("[LIMIT BUY] Partial fill. Cancelled the rest.", YELLOW)

                        exec_qty = float(status.get("executedQty", quantity) or 0)
                        cum_quote = float(status.get("cummulativeQuoteQty", 0) or 0)
                        entry_price = (cum_quote / exec_qty) if exec_qty > 0 and cum_quote > 0 else limit_price
                        quantity = exec_qty
                        log(f"[BUY FILLED]   |  {active_symbol}", GREEN)
                        log(f"   Entry Price  : {entry_price:.6f} USDT", WHITE)
                        log(f"   Quantity     : {quantity} {base_asset}", WHITE)

                        # Wait 1 second for balance to settle then read actual free qty.
                        # Binance deducts trading fees from the received asset so the
                        # real free balance is slightly less than executedQty.
                        # Using the actual free balance prevents OCO from being
                        # rejected for insufficient quantity.
                        time.sleep(1)
                        try:
                            actual_qty = trader.get_balance(base_asset)
                            if actual_qty >= trader.min_qty:
                                quantity = trader._round_qty(actual_qty)
                                log(f"   OCO Qty (net of fees): {quantity} {base_asset}", DIM)
                        except Exception as balance_err:
                            # The executed quantity is still safer than
                            # treating a filled buy as failed/untracked.
                            log(f"⚠️ Could not refresh filled balance: {balance_err}", YELLOW)

                        buy_ok           = True
                        in_position      = True
                        risk_mgr         = RiskManager(entry_price, -dyn_sl, dyn_tp)
                        run_bot._peak_price = entry_price
                        entry_time       = time.time()
                        peak_profit_ever = 0.0
                        trail_sl_price   = None
                        daily_trades    += 1
                        order_list_id    = None
                        signal_rsi       = rsi
                        signal_vol       = vol_ratio
                        signal_ob        = ob_ratio
                        tp_alert_sent    = False
                        sl_alert_sent    = False
                        tp_order_id   = None
                        sl_order_id   = None
                        # A buy may fill even if the following API request
                        # fails. Persist it before attempting the OCO so a
                        # restart cannot forget an owned asset.
                        persist_runtime_state()

                    except Exception as e:
                        log(f"[BUY FAILED]: {e}", RED)
                        send_telegram(
                            f"🚨 <b>BUY FAILED</b>\n\n"
                            f"<b>Pair:</b> {active_symbol}\n"
                            f"<b>Error:</b> {e}"
                        )

                    # ── STEP 2: Place OCO ──────────────────────────────────
                    if buy_ok:
                        take_profit_price = entry_price * (1 + dyn_tp / 100)
                        stop_loss_price   = entry_price * (1 - dyn_sl / 100)

                        oco_placed = False
                        for attempt in range(1, 4):
                            try:
                                log(f"Placing OCO order... (attempt {attempt}/3)", YELLOW)
                                oco_order     = trader.place_oco_order(quantity, take_profit_price, stop_loss_price)
                                order_list_id = oco_order["orderListId"]
                                oco_placed    = True

                                tp_order_id = None
                                sl_order_id = None
                                order_entries = oco_order.get("orderReports") or oco_order.get("orders") or []
                                for o in order_entries:
                                    otype = o.get("type", "")
                                    if otype == "LIMIT_MAKER":
                                        tp_order_id = o["orderId"]
                                    elif otype in ["STOP_LOSS", "STOP_LOSS_LIMIT"]:
                                        sl_order_id = o["orderId"]

                                if tp_order_id is None or sl_order_id is None:
                                    time.sleep(1)
                                    open_ords = client.get_open_orders(symbol=active_symbol)
                                    for o in open_ords:
                                        if o.get("orderListId") == order_list_id:
                                            otype = o.get("type", "")
                                            if otype == "LIMIT_MAKER":
                                                tp_order_id = o["orderId"]
                                            elif otype in ["STOP_LOSS", "STOP_LOSS_LIMIT"]:
                                                sl_order_id = o["orderId"]

                                log(f"   Stop Loss @  : {risk_mgr.stop_loss_price:.6f}", RED)
                                log(f"   Take Profit @: {risk_mgr.take_profit_price:.6f}", GREEN)
                                log(f"   OCO List ID  : {order_list_id}", CYAN)
                                persist_runtime_state()

                                send_telegram(
                                    f"🚀 <b>BUY FILLED — OCO ACTIVE</b>\n\n"
                                    f"<b>Pair:</b> {active_symbol}\n"
                                    f"<b>Entry Price:</b> {entry_price:.6f} USDT\n"
                                    f"<b>Qty:</b> {quantity} {base_asset}\n\n"
                                    f"🎯 <b>Take Profit:</b> {risk_mgr.take_profit_price:.6f} (+{dyn_tp}%)\n"
                                    f"🛡️ <b>Stop Loss:</b>   {risk_mgr.stop_loss_price:.6f} (-{dyn_sl}%)\n\n"
                                    f"💰 <b>Trade Amount:</b> {trade_amount:.2f} USDT"
                                )
                                divider("-")
                                log(f"Monitoring {active_symbol} OCO exit...", CYAN)
                                last_tg_update = 0  # reset so first monitor update fires immediately
                                break

                            except Exception as oco_err:
                                log(f"[OCO attempt {attempt} FAILED]: {oco_err}", RED)
                                if attempt < 3:
                                    log("Retrying OCO in 3 seconds...", YELLOW)
                                    time.sleep(3)

                        if not oco_placed:
                            log("[CRITICAL] OCO could not be placed. Closing position to avoid an unprotected trade.", RED)
                            send_telegram(
                                f"🚨 <b>OCO FAILED — Closing Position</b>\n\n"
                                f"<b>Pair:</b> {active_symbol}\n"
                                f"<b>Entry:</b> {entry_price:.6f}\n"
                                f"⚠️ Exchange-side protection was unavailable, so the bot is selling now."
                            )
                            sold = execute_exit(trader, base_asset, "OCO_SETUP_FAILED", entry_price)
                            if sold:
                                exit_pnl_pct = net_pnl_pct(entry_price, sold)
                                daily_pnl_pct += exit_pnl_pct
                                if exit_pnl_pct >= 0:
                                    daily_wins += 1
                                else:
                                    daily_losses += 1
                                log_trade(
                                    active_symbol, entry_price, sold, quantity,
                                    "OCO_SETUP_FAILED", signal_rsi, signal_vol, signal_ob, entry_time,
                                )
                                in_position      = False
                                entry_time       = None
                                trail_sl_price   = None
                                peak_profit_ever = 0.0
                                last_tg_update   = 0
                                persist_runtime_state()
                                log("Position closed after OCO failure. Returning to scan mode.", YELLOW)
                            else:
                                log(
                                    "[CRITICAL] Emergency sell failed. Position remains open without an OCO; "
                                    "manual action is required.",
                                    RED,
                                )

                # Heartbeat when scanning (no active trade)
                if time.time() - last_heartbeat >= HEARTBEAT_SEC:
                    try:
                        send_telegram(
                            f"🤖 <b>Bot Heartbeat</b>\n\n"
                            f"✅ Bot is running normally\n"
                            f"🔍 Status    : Scanning market\n"
                            f"📊 Trades today: {daily_trades} | W:{daily_wins} L:{daily_losses}\n"
                            f"💰 Daily P&L : {daily_pnl_pct:+.2f}%\n"
                            f"💵 Wallet    : {usdt_balance:.2f} USDT",
                            silent=True
                        )
                        last_heartbeat = time.time()
                        log("[Heartbeat] 3-hour alive ping sent", DIM)
                    except Exception:
                        pass

                time.sleep(config.SIGNAL_SCAN_SEC)

            # ── IN POSITION: Monitor OCO status ───────────────
            else:
                current_price = trader.get_current_price()
                pnl_pct       = risk_mgr.get_pnl_pct(current_price)
                now           = datetime.now().strftime("%H:%M:%S")

                # ── TRADE TIMER ───────────────────────────────
                time_in_trade = time.time() - entry_time if entry_time else 0
                if pnl_pct > peak_profit_ever:
                    peak_profit_ever = pnl_pct

                # ── SMART ALERTS (phone notifications) ────────
                try:
                    tp_dist_pct = (risk_mgr.take_profit_price - current_price) / current_price * 100
                    sl_dist_pct = (current_price - risk_mgr.stop_loss_price) / current_price * 100

                    # TP proximity — almost there!
                    if tp_dist_pct <= 0.5 and not tp_alert_sent:
                        send_telegram(
                            f"🎯 <b>Almost at Take Profit!</b>\n\n"
                            f"🪙 <b>{active_symbol}</b>\n"
                            f"💰 Current : {current_price:.4f}\n"
                            f"🎯 TP target: {risk_mgr.take_profit_price:.4f}\n"
                            f"📏 Distance : {tp_dist_pct:.2f}% away\n"
                            f"📈 P&L now  : {pnl_pct:+.3f}%"
                        )
                        tp_alert_sent = True
                        log(f"[Alert] TP proximity alert sent — {tp_dist_pct:.2f}% away", GREEN)

                    # SL proximity — danger warning
                    if sl_dist_pct <= 0.5 and not sl_alert_sent:
                        send_telegram(
                            f"⚠️ <b>Price Near Stop Loss!</b>\n\n"
                            f"🪙 <b>{active_symbol}</b>\n"
                            f"💰 Current  : {current_price:.4f}\n"
                            f"🛡️ SL target : {risk_mgr.stop_loss_price:.4f}\n"
                            f"📏 Distance  : {sl_dist_pct:.2f}% away\n"
                            f"📉 P&L now   : {pnl_pct:+.3f}%\n\n"
                            f"Bot will auto-sell if SL is hit."
                        )
                        sl_alert_sent = True
                        log(f"[Alert] SL proximity alert sent — {sl_dist_pct:.2f}% away", RED)

                    # Reset TP alert if price pulls back (might approach again)
                    if tp_dist_pct > 1.0:
                        tp_alert_sent = False

                    # Heartbeat every 3 hours — "bot is alive"
                    if time.time() - last_heartbeat >= HEARTBEAT_SEC:
                        send_telegram(
                            f"🤖 <b>Bot Heartbeat</b>\n\n"
                            f"✅ Bot is running normally\n"
                            f"🪙 In trade  : {active_symbol}\n"
                            f"📈 P&L now   : {pnl_pct:+.3f}%\n"
                            f"🏆 Peak P&L  : {peak_profit_ever:+.3f}%\n"
                            f"🎯 TP        : {risk_mgr.take_profit_price:.4f} ({tp_dist_pct:.2f}% away)\n"
                            f"💰 Entry     : {entry_price:.4f}\n"
                            f"⏱ In trade  : {int(time_in_trade//3600)}h {int((time_in_trade%3600)//60)}m",
                            silent=True
                        )
                        last_heartbeat = time.time()
                        log("[Heartbeat] 3-hour alive ping sent", DIM)

                except Exception:
                    pass  # never block trading due to alert failure

                # ── GUARD 1: MIN HOLD TIME (5 min) ────────────
                # Don't let OCO SL fire in first 5 minutes — fake signals
                # We handle this by not acting on manual monitor; OCO on
                # Binance side still protects. Just log it.
                if time_in_trade < MIN_HOLD_SEC:
                    mins = int(time_in_trade // 60)
                    secs = int(time_in_trade % 60)
                    log(f"[Hold Timer] {mins}m {secs}s in trade (min hold: {MIN_HOLD_SEC//60}m) | Peak: {peak_profit_ever:+.2f}%", DIM)

                # ── GUARD 2: STALE-LOSER EXIT AFTER ONE 4h CANDLE ─
                # Exit only if a full 4h bar passed, the trade never showed
                # a real bounce, and it is at least 1% underwater.
                if (time_in_trade >= MAX_NO_PROFIT_SEC
                        and peak_profit_ever < 0.6
                        and pnl_pct <= -1.0
                        and order_list_id is not None):
                    log(
                        f"[Time Exit] {MAX_NO_PROFIT_SEC//3600}h stale loser "
                        f"(peak {peak_profit_ever:+.2f}%, now {pnl_pct:+.2f}%) — exiting",
                        YELLOW
                    )
                    try:
                        open_ords = client.get_open_orders(symbol=active_symbol)
                        for o in open_ords:
                            try: client.cancel_order(symbol=active_symbol, orderId=o['orderId'])
                            except Exception: pass
                        time.sleep(1)
                    except Exception as e:
                        log(f"Could not cancel OCO: {e}", DIM)
                    sold = execute_exit(trader, base_asset, "TIME_EXIT_4H", entry_price)
                    if sold:
                        exit_pnl_pct = net_pnl_pct(entry_price, sold)
                        log_trade(active_symbol, entry_price, sold,
                                  quantity, "TIME_EXIT_4H", signal_rsi, signal_vol, signal_ob, entry_time)
                        in_position      = False
                        peak_profit_ever = 0.0
                        entry_time       = None
                        trail_sl_price   = None
                        last_tg_update   = 0
                        daily_pnl_pct   += exit_pnl_pct
                        if exit_pnl_pct >= 0: daily_wins   += 1
                        else:            daily_losses  += 1
                        # Block dead coin for 30 min — it went nowhere, no point re-buying
                        sl_cooldown[active_symbol] = time.time()
                        log(f"[Cooldown] {active_symbol} blocked 30 min after time exit", YELLOW)
                        persist_runtime_state()
                        log("Returning to scanning mode...", YELLOW)
                        divider("-")
                    time.sleep(config.CHECK_INTERVAL_SEC)
                    continue

                # ── GUARD 3: TRAILING PROFIT LOCK ────────────
                # Fee-aware trail: only lock profits that remain positive after round-trip fees.
                if order_list_id is not None:
                    new_trail_sl = None
                    if pnl_pct >= 4.0:
                        new_trail_sl = entry_price * 1.028  # lock 2.8% at 4.0%
                    elif pnl_pct >= 3.0:
                        new_trail_sl = entry_price * 1.020  # lock 2.0% at 3.0%
                    elif pnl_pct >= 2.0:
                        new_trail_sl = entry_price * 1.012  # lock 1.2% at 2.0%
                    elif pnl_pct >= 1.5:
                        new_trail_sl = entry_price * 1.008  # lock 0.8% at 1.5%

                    if new_trail_sl is not None:
                        if trail_sl_price is None or new_trail_sl > trail_sl_price:
                            trail_sl_price = new_trail_sl
                            log(f"[Trail Lock] Profit at {pnl_pct:+.2f}% — SL raised to {trail_sl_price:.4f} (locked profit)", GREEN)

                    # If price falls below the trailed SL, exit now
                    if trail_sl_price is not None and current_price <= trail_sl_price:
                        log(f"[Trail Lock] Price {current_price:.4f} hit trail SL {trail_sl_price:.4f} — locking profit!", YELLOW)
                        try:
                            open_ords = client.get_open_orders(symbol=active_symbol)
                            for o in open_ords:
                                try: client.cancel_order(symbol=active_symbol, orderId=o['orderId'])
                                except Exception: pass
                            time.sleep(1)
                        except Exception as e:
                            log(f"Could not cancel OCO: {e}", DIM)
                        sold = execute_exit(trader, base_asset, "TRAIL_LOCK", entry_price)
                        if sold:
                            exit_pnl_pct = net_pnl_pct(entry_price, sold)
                            log_trade(active_symbol, entry_price, sold,
                                      quantity, "TRAIL_LOCK", signal_rsi, signal_vol, signal_ob, entry_time)
                            in_position      = False
                            peak_profit_ever = 0.0
                            entry_time       = None
                            trail_sl_price   = None
                            run_bot._peak_price = 0
                            last_tg_update   = 0
                            daily_pnl_pct   += exit_pnl_pct
                            if exit_pnl_pct >= 0:
                                daily_wins += 1
                            else:
                                daily_losses += 1
                            persist_runtime_state()
                            log("Returning to scanning mode...", YELLOW)
                            divider("-")
                        time.sleep(config.CHECK_INTERVAL_SEC)
                        continue

                # ── GUARD 4: PROFIT-PROTECT GUARD ─────────────
                # Exit only after a meaningful run-up and still-positive net gain.
                if not hasattr(run_bot, '_peak_price') or current_price > run_bot._peak_price:
                    run_bot._peak_price = current_price
                peak_pnl = ((run_bot._peak_price - entry_price) / entry_price) * 100
                # Only bank a move that is still a real gain after fees.
                # The old rule sold as soon as a +1.2% spike fell back through +0.35%,
                # which included losing trades and cut every bounce short.
                if (peak_pnl >= 2.0
                        and pnl_pct >= 0.9
                        and order_list_id is not None
                        and trail_sl_price is None):
                    log(
                        f"[Profit Protect] Peak +{peak_pnl:.2f}%, now {pnl_pct:+.2f}% — securing gain",
                        YELLOW
                    )
                    try:
                        open_ords = client.get_open_orders(symbol=active_symbol)
                        for o in open_ords:
                            try: client.cancel_order(symbol=active_symbol, orderId=o['orderId'])
                            except Exception: pass
                        time.sleep(1)
                    except Exception as e:
                        log(f"Could not cancel OCO: {e}", DIM)
                    sold = execute_exit(trader, base_asset, "NO_LOSS_GUARD", entry_price)
                    if sold:
                        exit_pnl_pct = net_pnl_pct(entry_price, sold)
                        log_trade(active_symbol, entry_price, sold,
                                  quantity, "NO_LOSS_GUARD", signal_rsi, signal_vol, signal_ob, entry_time)
                        in_position      = False
                        peak_profit_ever = 0.0
                        entry_time       = None
                        trail_sl_price   = None
                        run_bot._peak_price = 0
                        last_tg_update   = 0
                        daily_pnl_pct   += exit_pnl_pct
                        if exit_pnl_pct >= 0: daily_wins   += 1
                        else:            daily_losses  += 1
                        persist_runtime_state()
                        log("Returning to scanning mode...", YELLOW)
                        divider("-")
                    time.sleep(config.CHECK_INTERVAL_SEC)
                    continue

                pnl_color = GREEN if pnl_pct >= 0 else RED
                print(f"{DIM}[{now}]{RESET} {active_symbol} | "
                      f"P&L: {pnl_color}{pnl_pct:+.3f}%{RESET} | "
                      f"Price: {current_price:.6f} | "
                      f"TP: {risk_mgr.take_profit_price:.6f} | "
                      f"SL: {risk_mgr.stop_loss_price:.6f}",
                      flush=True)

                # Send Telegram P&L update every 5 minutes — NOT every loop tick
                now_ts = time.time()
                if now_ts - last_tg_update >= TG_UPDATE_INTERVAL:
                    try:
                        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                        pnl_text  = f"+{pnl_pct:.3f}%" if pnl_pct >= 0 else f"{pnl_pct:.3f}%"
                        send_telegram(
                            f"📈 <b>Position Update</b>\n\n"
                            f"🪙 <b>Pair:</b> #{active_symbol}\n"
                            f"💰 <b>Price:</b> {current_price:.6f} USDT\n"
                            f"📉 <b>Entry:</b> {entry_price:.6f} USDT\n"
                            f"🚦 <b>P&amp;L:</b> {pnl_emoji} <b>{pnl_text}</b>\n\n"
                            f"🎯 <b>TP:</b> {risk_mgr.take_profit_price:.6f}\n"
                            f"🛡️ <b>SL:</b> {risk_mgr.stop_loss_price:.6f}",
                            silent=True
                        )
                        last_tg_update = now_ts
                    except Exception:
                        pass

                # ── MANUAL MONITORING (OCO placement failed) ──
                if order_list_id is None:
                    status = risk_mgr.check(current_price)
                    if status in ("STOP_LOSS", "TAKE_PROFIT"):
                        sold = execute_exit(trader, base_asset, status, entry_price)
                        if sold:
                            exit_pnl_pct = net_pnl_pct(entry_price, sold)
                            daily_pnl_pct += exit_pnl_pct
                            if exit_pnl_pct >= 0:
                                daily_wins += 1
                            else:
                                daily_losses += 1
                            log_trade(
                                active_symbol, entry_price, sold, quantity,
                                status, signal_rsi, signal_vol, signal_ob, entry_time,
                            )
                            in_position    = False
                            last_tg_update = 0
                            persist_runtime_state()
                            log("Returning to scanning mode...", YELLOW)
                            divider("-")
                        # If sell failed, stay in_position=True and retry next cycle
                    time.sleep(config.CHECK_INTERVAL_SEC)
                    continue

                # ── CHECK OCO STATUS ON BINANCE ───────────────
                try:
                    status_info = client.v3_get_order_list(orderListId=order_list_id)
                    list_status = status_info.get("listOrderStatus", "EXECUTING")
                except Exception as e:
                    try:
                        open_orders = client.get_open_orders()
                        has_oco = any(o.get('orderListId') == order_list_id for o in open_orders)
                        list_status = "EXECUTING" if has_oco else "ALL_DONE"
                    except Exception as fallback_err:
                        list_status = "EXECUTING"
                        log(f"⚠️ OCO status check failed: {fallback_err}. Retrying...", YELLOW)

                if list_status == "ALL_DONE":
                    log("Exit order filled! Checking OCO results...", GREEN)
                    reason     = "UNKNOWN"
                    exit_price = current_price

                    try:
                        tp_order = client.get_order(symbol=active_symbol, orderId=tp_order_id)
                        sl_order = client.get_order(symbol=active_symbol, orderId=sl_order_id)

                        if tp_order["status"] == "FILLED":
                            reason     = "TAKE_PROFIT"
                            # LIMIT_MAKER fills at the exact limit price
                            exit_price = float(tp_order["price"])

                        elif sl_order["status"] == "FILLED":
                            reason    = "STOP_LOSS"
                            exec_qty  = float(sl_order.get("executedQty", 0))
                            cum_quote = float(sl_order.get("cummulativeQuoteQty", 0))
                            if exec_qty > 0 and cum_quote > 0:
                                exit_price = cum_quote / exec_qty  # actual weighted avg fill
                            else:
                                exit_price = float(sl_order.get("stopPrice", current_price))
                    except Exception as e:
                        log(f"Could not fetch OCO fill details: {e}", DIM)

                    if reason == "UNKNOWN":
                        # ALL_DONE also means an OCO was cancelled or expired.
                        # Verify an actual sell fill before forgetting a position.
                        held_qty = trader._round_qty(trader.get_balance(base_asset))
                        if held_qty >= trader.min_qty:
                            log(
                                "⚠️ OCO ended without a sell fill; position is still held. "
                                "Recreating exchange-side protection.",
                                RED,
                            )
                            try:
                                replacement = trader.place_oco_order(
                                    held_qty,
                                    risk_mgr.take_profit_price,
                                    risk_mgr.stop_loss_price,
                                )
                                order_list_id = replacement["orderListId"]
                                tp_order_id = None
                                sl_order_id = None
                                for order in replacement.get("orderReports") or replacement.get("orders") or []:
                                    if order.get("type") == "LIMIT_MAKER":
                                        tp_order_id = order["orderId"]
                                    elif order.get("type") in ("STOP_LOSS", "STOP_LOSS_LIMIT"):
                                        sl_order_id = order["orderId"]
                                quantity = held_qty
                                persist_runtime_state()
                                continue
                            except Exception as protect_err:
                                log(
                                    f"🚨 Could not replace canceled OCO: {protect_err}. "
                                    "Holding position and retrying protection next cycle.",
                                    RED,
                                )
                                send_telegram(
                                    f"🚨 <b>OCO ENDED WITHOUT SELL FILL</b>\n"
                                    f"{active_symbol} is still held. Retrying OCO placement: {protect_err}"
                                )
                                # Keep in_position=True and state on disk. Never
                                # call it closed merely because the list ended.
                                time.sleep(config.CHECK_INTERVAL_SEC)
                                continue

                    pnl_pct  = net_pnl_pct(entry_price, exit_price)
                    pnl_text = f"+{pnl_pct:.2f}% PROFIT 🟢" if pnl_pct >= 0 else f"{pnl_pct:.2f}% LOSS 🔴"

                    divider("=", BOLD)
                    log(f">> EXIT TRIGGERED (OCO)  ->  {reason}", BOLD)
                    divider("=", BOLD)
                    log(f"[OCO EXITED]  |  Target Hit: {reason}", GREEN if pnl_pct >= 0 else RED)
                    log(f"   Entry Price : {entry_price:.6f} USDT", WHITE)
                    log(f"   Exit Price  : {exit_price:.6f} USDT", WHITE)

                    if pnl_pct >= 0:
                        log(f"   Final P&L   : +{pnl_pct:.2f}%  PROFIT", GREEN)
                    else:
                        log(f"   Final P&L   : {pnl_pct:.2f}%  LOSS", RED)

                    time.sleep(2)
                    final_usdt = trader.get_balance("USDT")
                    log(f"USDT Balance : {final_usdt:.4f} USDT", CYAN)

                    send_telegram(
                        f"🔔 <b>TRADE CLOSED (OCO)</b>\n\n"
                        f"<b>Pair:</b> {base_asset}USDT\n"
                        f"<b>Reason:</b> {reason}\n"
                        f"<b>Entry:</b> {entry_price:.6f}\n"
                        f"<b>Exit:</b> {exit_price:.6f}\n"
                        f"<b>P&amp;L:</b> {pnl_text}\n"
                        f"<b>Wallet:</b> {final_usdt:.4f} USDT"
                    )

                    in_position      = False
                    peak_profit_ever = 0.0
                    entry_time_snap  = entry_time
                    entry_time       = None
                    trail_sl_price   = None
                    last_tg_update   = 0
                    daily_pnl_pct   += pnl_pct
                    if pnl_pct >= 0: daily_wins   += 1
                    else:            daily_losses  += 1
                    log_trade(active_symbol, entry_price, exit_price, quantity,
                              reason, signal_rsi, signal_vol, signal_ob, entry_time_snap)
                    if reason == "STOP_LOSS":
                        sl_cooldown[active_symbol] = time.time()
                        log(f"[Cooldown] {active_symbol} blocked 30 min after SL hit", YELLOW)
                    elif reason == "TAKE_PROFIT":
                        # Block same coin for 10 min after TP — it just pumped, don't chase
                        sl_cooldown[active_symbol] = time.time() - 6600  # 7200 - 600 = 10 min block
                        log(f"[Cooldown] {active_symbol} blocked 10 min after TP — avoid chasing", YELLOW)
                    persist_runtime_state()
                    log(f"Daily P&L: {daily_pnl_pct:+.2f}% | Trades: {daily_trades} | W:{daily_wins} L:{daily_losses}", CYAN)
                    log("Returning to scanning mode...", YELLOW)
                    divider("-")

                else:
                    # Safety: if base asset is gone (manually sold), clear state
                    try:
                        account  = client.get_account()
                        base_bal = 0.0
                        for b in account["balances"]:
                            if b["asset"] == base_asset:
                                base_bal = float(b["free"]) + float(b["locked"])
                                break
                        if base_bal < trader.min_qty:
                            log("⚠️ Base asset balance gone — trade closed externally.", YELLOW)
                            try:
                                client.cancel_order_list(symbol=active_symbol, orderListId=order_list_id)
                                log("Cancelled remaining OCO orders.", DIM)
                            except Exception:
                                pass
                            send_telegram(
                                f"⚠️ <b>Position Closed Externally</b>\n\n"
                                f"<b>Pair:</b> {active_symbol}\n"
                                f"Balance of {base_asset} is gone. OCO cancelled.\n"
                                f"Bot returning to scan mode."
                            )
                            in_position   = False
                            last_tg_update = 0
                            persist_runtime_state()
                            log("Returning to scanning mode...", YELLOW)
                            divider("-")
                    except Exception as e:
                        log(f"⚠️ Balance check failed: {e}", DIM)

                time.sleep(config.CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            log("\n⛔ Stopped by user.", YELLOW)
            if in_position:
                try:
                    log("Cancelling active OCO order...", YELLOW)
                    client.cancel_order_list(symbol=active_symbol, orderListId=order_list_id)
                except Exception as e:
                    log(f"Could not cancel OCO: {e}", DIM)
                execute_exit(trader, base_asset, "MANUAL STOP", entry_price)
            send_telegram("⛔ <b>Bot Stopped</b>\nManually stopped by user.")
            break

        except Exception as e:
            log(f"⚠️ Error: {e}", YELLOW)
            send_telegram(f"⚠️ <b>Bot Error</b>\n{e}\nRetrying in 15 seconds...")
            time.sleep(15)


if __name__ == "__main__":
    run_bot()
