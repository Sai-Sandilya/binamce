# =============================================================
#   BINANCE SPOT GRID TRADING BOT — CORE ENGINE
#   Calculates grid levels, handles initial allocation,
#   places orders, and executes Buy Low | Sell High cycles.
# =============================================================

import time
import math
import importlib
from datetime import datetime
import requests
from binance.client import Client
import config
from trader import Trader

# Console Color Constants
GREEN  = "\033[1;32m"
RED    = "\033[1;31m"
YELLOW = "\033[1;33m"
CYAN   = "\033[1;36m"
WHITE  = "\033[1;37m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def log(message: str, color: str = WHITE):
    print(f"{color}{message}{RESET}")

def divider(char: str = "-", bold: bool = False):
    style = BOLD if bold else ""
    print(f"{style}{char * 60}{RESET}")

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
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        log(f"⚠️ Telegram notification failed: {e}", RED)

def calculate_grid_prices(lower_limit: float, upper_limit: float, levels: int) -> list:
    """Calculate exact price levels for the grid using linear spacing."""
    if levels < 2:
        raise ValueError("Grid levels must be at least 2.")
    prices = []
    step = (upper_limit - lower_limit) / (levels - 1)
    for i in range(levels):
        prices.append(lower_limit + (i * step))
    return prices

def initialize_grid(trader: Trader, grid_prices: list):
    """
    Initialize the grid: cancel old orders, calculate levels, 
    perform asset allocation, and place limits.
    """
    log("Initializing Spot Grid...", CYAN)
    divider("-")

    # Cancel all existing open orders for this symbol first
    log("Cancelling existing open orders for symbol...", YELLOW)
    try:
        open_orders = trader.client.get_open_orders(symbol=trader.symbol)
        if open_orders:
            trader.client.cancel_open_orders(symbol=trader.symbol)
            log(f"   Cancelled {len(open_orders)} open orders.", DIM)
        else:
            log("   No open orders found.", DIM)
    except Exception as e:
        log(f"⚠️ Could not cancel open orders on startup: {e}. Continuing...", YELLOW)

    current_price = trader.get_current_price()
    log(f"Current Price of {trader.symbol}: {current_price:.6f} USDT", WHITE)

    # Separate into buy levels and sell levels
    buy_levels = [p for p in grid_prices if p < current_price]
    sell_levels = [p for p in grid_prices if p > current_price]

    log(f"Grid Configuration:", WHITE)
    log(f"   Buy Levels : {len(buy_levels)} orders below current price", DIM)
    log(f"   Sell Levels: {len(sell_levels)} orders above current price", DIM)

    # ── INITIAL ASSET ALLOCATION ──
    base_asset = trader.symbol.replace("USDT", "")
    free_base = trader.get_balance(base_asset)
    
    # Calculate total base quantity needed for sell levels
    qty_per_level = trader._round_qty(config.GRID_AMOUNT_USDT / current_price)
    total_base_needed = qty_per_level * len(sell_levels)
    
    log(f"Asset Allocation Check:", WHITE)
    log(f"   Current {base_asset} Balance: {free_base:.6f}", DIM)
    log(f"   Required {base_asset} for Sell Grid: {total_base_needed:.6f}", DIM)

    # If we don't have enough base asset, we need to buy the missing amount at market!
    missing_base = total_base_needed - free_base
    if missing_base > trader.min_qty:
        needed_usdt = missing_base * current_price * 1.02 # Add a 2% buffer
        free_usdt = trader.get_balance("USDT")
        
        log(f"   Missing {base_asset}: {missing_base:.6f} (Need to buy at market using ~{needed_usdt:.2f} USDT)", YELLOW)
        
        if free_usdt < needed_usdt:
            raise ValueError(
                f"Insufficient USDT balance to perform initial asset allocation!\n"
                f"Need: {needed_usdt:.2f} USDT | Available: {free_usdt:.2f} USDT."
            )
        
        log(f"Executing market buy to allocate {missing_base:.6f} {base_asset}...", YELLOW)
        trader.client.create_order(
            symbol=trader.symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quantity=trader._round_qty(missing_base)
        )
        time.sleep(2) # Wait for execution to settle
        free_base = trader.get_balance(base_asset)
        log(f"   New {base_asset} Balance: {free_base:.6f}", GREEN)

    # ── PLACE LIMIT ORDERS ──
    active_grid_orders = {} # maps orderId -> { 'price': float, 'side': 'BUY'/'SELL', 'qty': float }
    
    # Place BUY Limit orders
    for p in buy_levels:
        qty = trader._round_qty(config.GRID_AMOUNT_USDT / p)
        p_rounded = trader._round_price(p)
        p_str = f"{p_rounded:.{max(0, int(round(-math.log(trader.tick_size, 10), 0)))}f}"
        
        log(f"Placing BUY Limit order at {p_str}...", YELLOW)
        order = trader.client.create_order(
            symbol=trader.symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_LIMIT,
            timeInForce=Client.TIME_IN_FORCE_GTC,
            quantity=qty,
            price=p_str
        )
        active_grid_orders[order["orderId"]] = {
            "price": p_rounded,
            "side": "BUY",
            "qty": qty
        }
        
    # Place SELL Limit orders
    for p in sell_levels:
        qty = trader._round_qty(config.GRID_AMOUNT_USDT / p)
        p_rounded = trader._round_price(p)
        p_str = f"{p_rounded:.{max(0, int(round(-math.log(trader.tick_size, 10), 0)))}f}"
        
        log(f"Placing SELL Limit order at {p_str}...", YELLOW)
        order = trader.client.create_order(
            symbol=trader.symbol,
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_LIMIT,
            timeInForce=Client.TIME_IN_FORCE_GTC,
            quantity=qty,
            price=p_str
        )
        active_grid_orders[order["orderId"]] = {
            "price": p_rounded,
            "side": "SELL",
            "qty": qty
        }

    divider("-")
    log("🎉 Spot Grid Initialized Successfully!", GREEN)
    return active_grid_orders

def run_grid_bot():
    client = Client(
        config.API_KEY,
        config.API_SECRET,
        testnet=config.TESTNET,
        requests_params={
            "timeout": float(getattr(config, "BINANCE_REQUEST_TIMEOUT_SEC", 10))
        },
    )
    
    # Fix for Timestamp -1021 error (sync local time with Binance server)
    try:
        server_time = client.get_server_time()
        client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
    except Exception as e:
        log(f"⚠️ Could not sync time with Binance: {e}", RED)
        
    trader = Trader(client, config.GRID_SYMBOL)
    
    divider("=", BOLD)
    log("   +-------------------------------------------------+", BOLD)
    log("   |          BINANCE SPOT GRID TRADING BOT v1.0     |", BOLD)
    log("   |      Systematic Buy Low | Sell High Engine      |", BOLD)
    log("   +-------------------------------------------------+", BOLD)
    divider("=", BOLD)
    
    log(f"Mode         : [{'TESTNET' if config.TESTNET else 'LIVE TRADING'}]", GREEN if not config.TESTNET else YELLOW)
    log(f"Coin Symbol  : {config.GRID_SYMBOL}", WHITE)
    log(f"Grid Range   : {config.GRID_LOWER_LIMIT} USDT to {config.GRID_UPPER_LIMIT} USDT", WHITE)
    log(f"Grid Levels  : {config.GRID_LEVELS}", WHITE)
    log(f"Size/Level   : {config.GRID_AMOUNT_USDT} USDT", WHITE)
    divider("-")
    
    # Calculate exact prices
    grid_prices = calculate_grid_prices(config.GRID_LOWER_LIMIT, config.GRID_UPPER_LIMIT, config.GRID_LEVELS)
    grid_prices = [trader._round_price(p) for p in grid_prices]
    
    # Send start Telegram notification
    send_telegram(
        f"📟 <b>Spot Grid Bot Started</b>\n\n"
        f"<b>Symbol:</b> #{config.GRID_SYMBOL}\n"
        f"<b>Range:</b> {config.GRID_LOWER_LIMIT} - {config.GRID_UPPER_LIMIT} USDT\n"
        f"<b>Levels:</b> {config.GRID_LEVELS}\n"
        f"<b>Allocated per Level:</b> {config.GRID_AMOUNT_USDT} USDT\n"
        f"<b>Mode:</b> {'TESTNET' if config.TESTNET else 'LIVE'}"
    )
    
    # Initialize the grid (places initial orders)
    active_orders = initialize_grid(trader, grid_prices)
    
    log("Monitoring active grid orders...", CYAN)
    divider("-")
    
    total_grid_profit = 0.0 # Track accumulative profits
    
    while True:
        try:
            # Live reload config in case user changes parameters
            importlib.reload(config)
            
            # Check status of every active order in our list
            order_ids = list(active_orders.keys())
            for oid in order_ids:
                o_details = active_orders[oid]
                
                # Query order from Binance
                order_info = client.get_order(symbol=trader.symbol, orderId=oid)
                status = order_info["status"]
                
                if status == "FILLED":
                    # An order was filled!
                    side = o_details["side"]
                    filled_price = o_details["price"]
                    qty = o_details["qty"]
                    
                    log(f"💥 GRID FILL: {side} order at {filled_price:.6f} USDT fully executed!", GREEN if side == "BUY" else CYAN)
                    
                    # Remove it from active orders
                    active_orders.pop(oid)
                    
                    # Calculate new order details
                    # Find where this price level is in our grid_prices array
                    idx = grid_prices.index(filled_price)
                    
                    if side == "BUY":
                        # If a BUY filled: place a corresponding SELL order one level above!
                        if idx < len(grid_prices) - 1:
                            new_price = grid_prices[idx + 1]
                            new_price_rounded = trader._round_price(new_price)
                            precision = max(0, int(round(-math.log(trader.tick_size, 10), 0)))
                            new_price_str = f"{new_price_rounded:.{precision}f}"
                            
                            log(f"Placing new SELL Limit order at {new_price_str}...", YELLOW)
                            new_order = client.create_order(
                                symbol=trader.symbol,
                                side=Client.SIDE_SELL,
                                type=Client.ORDER_TYPE_LIMIT,
                                timeInForce=Client.TIME_IN_FORCE_GTC,
                                quantity=qty,
                                price=new_price_str
                            )
                            active_orders[new_order["orderId"]] = {
                                "price": new_price_rounded,
                                "side": "SELL",
                                "qty": qty
                            }
                            
                            # Log and notify
                            send_telegram(
                                f"📥 <b>GRID BUY FILLED</b>\n\n"
                                f"<b>Symbol:</b> #{trader.symbol}\n"
                                f"<b>Price:</b> {filled_price:.6f} USDT\n"
                                f"<b>Quantity:</b> {qty:.6f}\n"
                                f"<b>Action:</b> Placed new SELL Limit at {new_price_rounded:.6f} USDT",
                                silent=True
                            )
                        else:
                            log("BUY filled at the very top of the grid! No level above to place SELL.", RED)
                            
                    elif side == "SELL":
                        # If a SELL filled: place a corresponding BUY order one level below!
                        if idx > 0:
                            new_price = grid_prices[idx - 1]
                            new_price_rounded = trader._round_price(new_price)
                            precision = max(0, int(round(-math.log(trader.tick_size, 10), 0)))
                            new_price_str = f"{new_price_rounded:.{precision}f}"
                            
                            log(f"Placing new BUY Limit order at {new_price_str}...", YELLOW)
                            new_order = client.create_order(
                                symbol=trader.symbol,
                                side=Client.SIDE_BUY,
                                type=Client.ORDER_TYPE_LIMIT,
                                timeInForce=Client.TIME_IN_FORCE_GTC,
                                quantity=qty,
                                price=new_price_str
                            )
                            active_orders[new_order["orderId"]] = {
                                "price": new_price_rounded,
                                "side": "BUY",
                                "qty": qty
                            }
                            
                            # Grid round-trip profit after fees (0.1% per side = 0.2% round-trip).
                            gross_profit = (filled_price - new_price) * qty
                            fee_cost = (filled_price + new_price) * qty * 0.001  # 0.1% each side
                            level_profit = gross_profit - fee_cost
                            total_grid_profit += level_profit

                            log(f"💰 ROUND-TRIP COMPLETED! Gross: +{gross_profit:.4f} USDT  Fees: -{fee_cost:.4f} USDT  Net: {level_profit:+.4f} USDT", GREEN if level_profit > 0 else RED)
                            
                            # Log and notify
                            send_telegram(
                                f"📤 <b>GRID SELL FILLED 💰</b>\n\n"
                                f"<b>Symbol:</b> #{trader.symbol}\n"
                                f"<b>Price:</b> {filled_price:.6f} USDT\n"
                                f"<b>Quantity:</b> {qty:.6f}\n"
                                f"<b>Profit:</b> +{level_profit:.4f} USDT\n"
                                f"<b>Total Grid Profit:</b> {total_grid_profit:.4f} USDT\n"
                                f"<b>Action:</b> Placed new BUY Limit at {new_price_rounded:.6f} USDT"
                            )
                        else:
                            log("SELL filled at the very bottom of the grid! No level below to place BUY.", RED)
                            
                elif status in ("CANCELED", "REJECTED", "EXPIRED"):
                    log(f"⚠️ Order {oid} was cancelled or rejected externally! Removing from grid...", YELLOW)
                    active_orders.pop(oid)
                    
            time.sleep(config.GRID_POLL_SEC)
            
        except KeyboardInterrupt:
            log("\n⛔ Stopped by user. Cancelling all active grid orders...", YELLOW)
            try:
                client.cancel_open_orders(symbol=trader.symbol)
                log("All active grid orders cancelled successfully.", GREEN)
            except Exception as e:
                log(f"Could not cancel active orders: {e}", DIM)
            send_telegram("⛔ <b>Spot Grid Bot Stopped</b>\nThe trading bot was manually stopped by the user.")
            break
            
        except Exception as e:
            log(f"⚠️ Grid Bot Error: {e}", YELLOW)
            send_telegram(f"⚠️ <b>Grid Bot Error</b>\n{e}\nRetrying in 15 seconds...")
            time.sleep(15)

if __name__ == "__main__":
    run_grid_bot()
