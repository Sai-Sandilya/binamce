# =============================================================
#   BINANCE AUTO TRADING BOT — CONFIGURATION
#   Credentials are loaded from environment variables / a .env
#   file — never hardcode secrets in this file. See .env.example
#   and BOT_RULES.md for setup instructions.
# =============================================================

import os

# Load a local .env file if python-dotenv is installed (optional).
# If it isn't installed, environment variables set on the system
# (e.g. via systemd EnvironmentFile, AWS instance env, etc.) are
# used directly.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and fill in your credentials, "
            f"or export {name} in your shell/AWS environment."
        )
    return value


# ── TELEGRAM NOTIFICATIONS ───────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_SEND_SCANS = True   # Send a Telegram alert whenever a BUY signal is found


# ── API CREDENTIALS ──────────────────────────────────────────
# Get testnet keys from: https://testnet.binance.vision/
# Get live keys from:    https://www.binance.com/en/my/settings/api-management
#
# SECURITY: Real Binance + Telegram credentials were previously
# hardcoded in this file and must be treated as compromised.
# Regenerate them in Binance API Management and via @BotFather
# BEFORE running this bot again. See BOT_RULES.md.
API_KEY    = _require_env("BINANCE_API_KEY")
API_SECRET = _require_env("BINANCE_API_SECRET")

# ── MODE ─────────────────────────────────────────────────────
TESTNET = False   # ← ALWAYS start with True. Set False only for live trading.
BOT_MODE = "SCREENER"  # Options: "SCREENER" (RSI/EMA bot) or "GRID" (Grid trading bot)

# Bound every Binance HTTP request. Without this, a stalled socket can freeze
# startup before the bot has reconciled positions or started scanning.
BINANCE_REQUEST_TIMEOUT_SEC = 10
# A second, process-level deadline for startup recovery requests.  On macOS
# this interrupts a socket read even if the HTTP client's timeout is ignored.
BINANCE_STARTUP_DEADLINE_SEC = 15

# ── TRADING PAIR ─────────────────────────────────────────────
# Options: "BNBUSDT", "BTCUSDT", etc. (Only used if MULTI_COIN = False)
SYMBOL = "BTCUSDT"

# ── MULTI-COIN SCANNER ───────────────────────────────────────
MULTI_COIN     = True    # If True, bot scans many coins. If False, it only trades SYMBOL above.
SCREENER_LIMIT = 30      # How many of the top volume coins to scan
COIN_BLACKLIST = [
    "LUNCUSDT", "LUNAUSDT", "USTCUSDT",   # Collapsed tokens
    "RLUSDUSDT", "USDCUSDT", "FDUSDUSDT", # Stablecoins — never move, can't profit
    "EURUSDT", "GBPUSDT", "USDTUSDT",     # Fiat-pegged pairs
    # Wrapped / synthetic assets with odd liquidity and tracking behavior
    "WBTCUSDT", "WBETHUSDT", "BNSOLUSDT",
]
MIN_PRICE      = 1.0    # Exclude all coins trading below $1.00
# Only buy alts when BTC itself is trending up but not already extended.
BTC_RSI_MIN    = 40
BTC_RSI_MAX    = 65

# ── TRADE AMOUNT ─────────────────────────────────────────────
# Percentage of your available USDT balance to use (e.g. 95 = 95%)
# Keeping a small % free avoids "insufficient balance" errors due to fees.
TRADE_AMOUNT_PCT = 30  # %

# ── RISK MANAGEMENT ──────────────────────────────────────────
# Binance charges ~0.1% fee per trade (0.2% round-trip).
# TP must exceed SL + 0.4% to have positive expected value at 50% win rate.
# Rule of thumb: TP should be at least 2x SL (after fees).
STOP_LOSS_PCT   = -2.0   # Wider than one noisy 5m wick
TAKE_PROFIT_PCT = 4.5    # Keep reward at least 2x the stop after fees

# ── STRATEGY SETTINGS ────────────────────────────────────────
# Every setting below is an active entry filter. A trade must satisfy all
# seven checks: trend, Fibonacci level, RSI range/divergence, MACD turn,
# Bollinger lower-band support, OBV/reversal, and a volume spike.
KLINE_INTERVAL = "4h"

RSI_PERIOD     = 14      # RSI lookback period
RSI_OVERSOLD   = 32      # Upper bound for a pullback entry
RSI_OVERSOLD_MIN = 20    # Below this is treated as a crash, not a pullback
RSI_OVERBOUGHT = 70
MIN_CONFIRMATIONS = 7    # All seven configured entry checks must pass
MAX_SPREAD_PCT = 0.25    # Skip pairs whose bid/ask spread is wider than this
MIN_REWARD_RISK = 1.5    # Swing-high target must pay at least this many times the stop

EMA_FAST = 50            # Daily trend EMA
EMA_SLOW = 200           # Daily trend EMA
ADX_PERIOD = 14          # Trend-strength lookback
ADX_MIN    = 20          # Below this the trend is too weak to buy
LIMIT_FILL_SEC = 120     # Cancel a limit buy if it is still open after this

# ── MACD SETTINGS ────────────────────────────────────────────
MACD_FAST          = 12  # Fast EMA for MACD
MACD_SLOW          = 26  # Slow EMA for MACD
MACD_SIGNAL_PERIOD = 9   # Signal line smoothing

# ── VOLUME CONFIRMATION ───────────────────────────────────────
# 1.3 = 30% above the 20-candle average (inside the 25–40% conviction band)
VOLUME_MULTIPLIER = 1.3

# ── BOLLINGER BANDS ───────────────────────────────────────────
BB_PERIOD  = 20          # SMA period for Bollinger middle band
BB_STD_DEV = 2.0         # Standard deviations for upper/lower bands


# ── TIMING ───────────────────────────────────────────────────
CHECK_INTERVAL_SEC = 10  # Check active trades every 10 seconds
SIGNAL_SCAN_SEC    = 15  # Scan for new opportunities every 15 seconds

# ── SPOT GRID SETTINGS ───────────────────────────────────────
# Only used if BOT_MODE = "GRID"
GRID_SYMBOL      = "BNBUSDT"  # The coin to run the grid on
GRID_LOWER_LIMIT = 635.0      # Bottom of the grid range (in USDT)
GRID_UPPER_LIMIT = 665.0      # Top of the grid range (in USDT)
GRID_LEVELS      = 5          # Number of grid buy/sell levels (minimum 2)
GRID_AMOUNT_USDT = 15.0       # USDT size per grid level (minimum is 10.0 USDT)
GRID_POLL_SEC    = 10         # How often to check active grid orders (seconds)

