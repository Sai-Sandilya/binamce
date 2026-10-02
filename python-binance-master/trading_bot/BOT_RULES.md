# Binance Spot Trading Bot — Complete Guide

## What This Bot Does

This bot scans the top 30 USDT trading pairs on Binance, finds coins that are oversold
(RSI < 30) with upward momentum (EMA9 > EMA21), buys the best one, then automatically
places an OCO order that takes profit at +2.5% or cuts the loss at -1.5%.

---

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point — run this to start |
| `bot.py` | Main trading loop — buy/monitor/sell logic |
| `config.py` | All settings — edit this to change behaviour |
| `strategy.py` | RSI + EMA indicator calculations |
| `screener.py` | Scans market for buy signals |
| `trader.py` | Executes orders on Binance (buy, sell, OCO) |
| `risk_manager.py` | Tracks P&L, checks stop loss / take profit |
| `grid_bot.py` | Alternative grid trading engine |

---

## How the Strategy Works (Step by Step)

### Entry Signal
Every 15 seconds the bot scans the top 30 coins by USDT volume.

For each coin it checks two conditions:

1. **RSI(14) < 30** — The coin is oversold. Price has dropped sharply.
   - RSI = 100 means price has been rising every candle
   - RSI = 0 means price has been falling every candle
   - RSI < 30 = extreme selling, historically precedes bounces
   - We use 5-minute candles (5m) to reduce noise

2. **EMA9 > EMA21** — The short-term trend is still bullish.
   - EMA9 = average of last 9 candles (fast, reacts quickly)
   - EMA21 = average of last 21 candles (slow, shows medium trend)
   - EMA9 > EMA21 means the coin is in an uptrend overall
   - Combining this with RSI<30 = buying a temporary dip in an uptrend

Only coins that pass BOTH conditions get a `BUY` signal. The coin with the lowest RSI
(most oversold) among all valid candidates is selected.

### Why This Combination?
- RSI<30 alone could mean a coin is in a long downtrend — dangerous
- EMA9>EMA21 alone means the trend is up, but it could be at the top
- Together they target **dips inside uptrends** — the highest-probability setup

### Position Sizing
- Bot uses 95% of available USDT balance per trade (leaves 5% as fee buffer)
- Only one position at a time — no diversification into multiple coins

### Exit — OCO Order
After buying, the bot immediately places an OCO (One-Cancels-Other) order on Binance:

```
Take Profit:  entry_price × 1.025   (+2.5%)
Stop Loss:    entry_price × 0.985   (-1.5%)
```

The OCO lives on Binance's servers. Even if the bot crashes or AWS restarts,
the OCO stays active and will exit the trade automatically.

### Fee Maths (Why TP=2.5% and SL=-1.5%)
Binance charges 0.1% per trade:
- Buy fee:  0.1%
- Sell fee: 0.1%
- Round-trip cost: **0.2%**

After fees:
- TP hit: +2.5% - 0.2% = **+2.3% net profit**
- SL hit: -1.5% - 0.2% = **-1.7% net loss**

Break-even win rate: 1.7 / (2.3 + 1.7) = **42.5%**
This means the bot only needs to win 43 out of every 100 trades to make money.
A typical RSI-bounce strategy in normal market conditions wins 45-55% of the time.

---

## Configuration (config.py)

```python
# RISK
STOP_LOSS_PCT   = -1.5    # Exit at -1.5% loss
TAKE_PROFIT_PCT =  2.5    # Exit at +2.5% profit
TRADE_AMOUNT_PCT = 95     # Use 95% of USDT balance per trade

# SIGNAL
KLINE_INTERVAL = "5m"     # 5-minute candles
RSI_PERIOD     = 14       # Standard RSI period
RSI_OVERSOLD   = 30       # BUY when RSI drops below this
EMA_FAST       = 9        # Fast EMA period
EMA_SLOW       = 21       # Slow EMA period

# SCREENER
MULTI_COIN     = True     # Scan multiple coins (recommended)
SCREENER_LIMIT = 30       # Number of top-volume coins to scan

# TIMING
SIGNAL_SCAN_SEC    = 15   # Seconds between scans when not in position
CHECK_INTERVAL_SEC = 10   # Seconds between checks when in position

# TELEGRAM
TELEGRAM_SEND_SCANS = True  # Send Telegram alert when BUY signal found
```

### How to Adjust Risk
If the bot is losing too much, make the stop loss tighter or take profits sooner:

| Want | Change |
|---|---|
| Less risk per trade | Lower `TRADE_AMOUNT_PCT` (e.g. 50%) |
| Tighter stop loss | Change `STOP_LOSS_PCT = -1.0` |
| Quicker profits | Change `TAKE_PROFIT_PCT = 1.5` |
| More signals | Change `RSI_OVERSOLD = 35` (looser, more trades, lower quality) |
| Fewer signals | Change `RSI_OVERSOLD = 25` (stricter, fewer trades, higher quality) |

**Important:** Always keep TP at least 1.5x the size of SL (ignoring the sign):
- SL = -1.5% means TP must be >= 2.25%
- SL = -1.0% means TP must be >= 1.5%

---

## Telegram Notifications

You will receive a Telegram message for every event:

| Event | Message | Silent? |
|---|---|---|
| Bot starts | Bot Started with settings summary | No (audible) |
| BUY signal found | Scan Update with RSI, EMA values | Yes (no sound) |
| Buy order filled + OCO placed | BUY FILLED - OCO ACTIVE with entry and targets | No |
| OCO fails to place | OCO FAILED - Manual Monitor Active | No |
| Position update every 5 min | Position Update with live P&L | Yes (no sound) |
| Trade closed profit | TRADE CLOSED +P&L | No |
| Trade closed loss | TRADE CLOSED -P&L | No |
| Sell fails | SELL FAILED - manual action required | No |
| External close detected | Position Closed Externally | No |
| Bot error | Bot Error with message | No |
| Bot stopped | Bot Stopped | No |

### To Set Up Telegram
1. Message @BotFather on Telegram
2. Type /newbot and follow prompts to get your TOKEN
3. Send any message to your new bot
4. Visit https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates to find your chat_id
5. Set on your AWS server:
   ```bash
   echo 'export TELEGRAM_TOKEN="your_token"' >> ~/.bashrc
   echo 'export TELEGRAM_CHAT_ID="your_chat_id"' >> ~/.bashrc
   source ~/.bashrc
   sudo systemctl restart tradingbot
   ```

---

## All Bug Fixes Applied (Full History)

### Bug 1 - Wrong entry price for OCO (trader.py)
**Before:** Used price snapshot taken BEFORE the buy order was placed.
**After:** Uses `cummulativeQuoteQty / executedQty` from the actual order fill response.
**Impact:** OCO stop loss and take profit levels are now set at the correct prices.

### Bug 2 - Fees made TP/SL a guaranteed losing setup (config.py)
**Before:** TP=0.8%, SL=-0.8%. After 0.2% fees: win=+0.6%, loss=-1.0%.
Required >62.5% win rate to break even. Impossible in practice.
**After:** TP=2.5%, SL=-1.5%. After fees: win=+2.3%, loss=-1.7%.
Break-even win rate = 42.5%. Achievable.

### Bug 3 - RSI oversold threshold too loose (config.py)
**Before:** RSI_OVERSOLD = 40. This is neutral territory, not oversold.
Generated constant false BUY signals on trending-down coins.
**After:** RSI_OVERSOLD = 30. Standard oversold definition.

### Bug 4 - Screener returned non-BUY coins as best candidate (screener.py)
**Before:** find_best_entry() returned ALL coins sorted by RSI including HOLD signals.
The most oversold coin was picked even without a confirmed BUY signal.
**After:** Only confirmed BUY signals are returned.

### Bug 5 - Grid bot reported fake profits (grid_bot.py)
**Before:** level_profit = (sell - buy) * qty. Fees not subtracted.
**After:** Subtracts 0.1% fee per side from reported profit.

### Bug 6 - OCO quantity too high - rejected by Binance (bot.py)
**Before:** OCO used executedQty from buy order (gross quantity before fees).
Binance deducts fees from received asset. Free balance < executedQty.
Binance rejected OCO for "insufficient balance."
**After:** Bot waits 1 second after buy, reads actual free balance, uses that for OCO.

### Bug 7 - execute_exit showed wrong P&L (bot.py)
**Before:** P&L calculated from current_price snapshot taken BEFORE sell order placed.
**After:** P&L calculated from cummulativeQuoteQty/executedQty of actual sell fill.

### Bug 8 - Telegram P&L monitor spammed every 10 seconds (bot.py)
**Before:** Position monitor sent Telegram message every 10 seconds = 360 messages/hour.
**After:** Position updates sent every 5 minutes (300 seconds). Still 12 per hour when in trade.

### Bug 9 - Startup Telegram used broken global variable (bot.py)
**Before:** Used a `global telegram_notified` variable that broke after importlib.reload(config).
**After:** Uses a local `startup_notified` boolean variable per process start.

### Bug 10 - RLUSDUSDT stablecoin pair (config.py + screener.py)
**Before:** RLUSD is Ripple's stablecoin. RLUSDUSDT barely moves +/-0.1%.
Capital was locked for 3 days with no possibility of hitting TP.
**After:** Added to COIN_BLACKLIST. Also added screener filter: skip coins with
less than 0.5% 24h price change.

### Bug 11 - Candle interval 1m too noisy (config.py)
**Before:** KLINE_INTERVAL = "1m". 1-minute candles have random noise. RSI<30 on 1m
fires very frequently on normal price wiggles, not real oversold conditions.
**After:** KLINE_INTERVAL = "5m". 5-minute candles give genuine oversold signals.

---

## AWS Server Management

### Server Details
- Instance: i-07e0e8ce5746e1a21
- IP: 52.195.172.11 (ap-northeast-1, Tokyo)
- OS: Ubuntu 24.04
- Bot Path: /home/ubuntu/bot/trading_bot/
- Venv: /home/ubuntu/bot/.venv/
- Service: tradingbot (systemd, auto-restart enabled)
- Logs: /home/ubuntu/bot_output.log

### SSH Access
```bash
ssh -i "aws-tokyo-key.pem.pem" ubuntu@52.195.172.11
```

### View Live Logs
```bash
tail -f ~/bot_output.log
```

### Check Bot Status
```bash
sudo systemctl status tradingbot
```

### Restart Bot
```bash
sudo systemctl restart tradingbot
```

### Stop Bot
```bash
sudo systemctl stop tradingbot
```

### Deploy Updated Files (from Windows PowerShell)
```powershell
scp -i "E:\downloads\aws-tokyo-key.pem.pem" `
  bot.py trader.py screener.py strategy.py risk_manager.py config.py `
  ubuntu@52.195.172.11:~/bot/trading_bot/

ssh -i "E:\downloads\aws-tokyo-key.pem.pem" ubuntu@52.195.172.11 `
  "sudo systemctl restart tradingbot"
```

---

## Blacklisted Coins

| Coin | Reason |
|---|---|
| LUNCUSDT, LUNAUSDT, USTCUSDT | Collapsed tokens, near-zero value |
| RLUSDUSDT, USDCUSDT, FDUSDUSDT | Stablecoins - price never moves enough to profit |
| EURUSDT, GBPUSDT, USDTUSDT | Fiat-pegged pairs |

Coins with less than 0.5% 24-hour price change are also automatically skipped
by the screener regardless of blacklist.

---

## Realistic Expectations

| Metric | Realistic Range |
|---|---|
| Win rate | 40-55% of trades |
| Net profit per winning trade | ~2.3% |
| Net loss per losing trade | ~1.7% |
| Trades per day | 0-3 (depends on market) |
| Expected at 50% win rate, 2 trades/day | ~+0.6% per day |

The bot will NOT trade every day. RSI<30 with upward EMA is a rare signal.
During sideways or strongly bearish markets, it may go days without a trade.
This is correct - waiting for the right setup beats forcing bad trades.

### When the Bot Will Profit
- Normal trending market with periodic pullbacks
- After a news-driven sell-off that quickly recovers
- Altcoin season: coins trend up with regular dips

### When the Bot Will Lose
- Strong sustained downtrend: coins keep falling after RSI<30 (failed bounces)
- Flash crash: price drops 5%+ instantly, SL fills below target (slippage)
- Choppy market: RSI oscillates around 30, triggering repeated SL hits

---

## Indicator Formulas

### RSI (Relative Strength Index) - Wilder Smoothing
```
deltas     = price[i] - price[i-1]  for each candle
gains      = max(delta, 0)
losses     = max(-delta, 0)

avg_gain   = SMA(gains, 14) for first value
avg_gain   = (prev_avg_gain * 13 + current_gain) / 14  for subsequent

avg_loss   = same method

RS         = avg_gain / avg_loss
RSI        = 100 - (100 / (1 + RS))
```
Range: 0-100. Below 30 = oversold. Above 70 = overbought.

### EMA (Exponential Moving Average)
```
k          = 2 / (period + 1)
first_EMA  = SMA of first `period` candles
EMA[i]     = price[i] * k + EMA[i-1] * (1 - k)
```
EMA9 > EMA21 means short-term trend is bullish.

### OCO Price Levels
```
Take Profit = entry_price * (1 + TAKE_PROFIT_PCT / 100)
Stop Loss   = entry_price * (1 + STOP_LOSS_PCT / 100)
```
Example: entry = 100.00 USDT, TP=2.5%, SL=-1.5%
```
Take Profit = 100.00 * 1.025 = 102.50 USDT
Stop Loss   = 100.00 * 0.985 =  98.50 USDT
```

### Net P&L After Fees
```
Net P&L (%) = gross_pnl_pct - 0.2%   (0.1% buy fee + 0.1% sell fee)
```

---

*Last updated: 2026-06-15*
