# =============================================================
#   STRATEGY — Professional Multi-Indicator Signal Engine
#
#   Indicators used:
#     1. RSI(14)              — oversold detection
#     2. EMA 9/21 cross       — trend direction filter
#     3. MACD(12,26,9)        — momentum reversal confirmation
#     4. Volume spike         — real buying interest filter
#     5. Bollinger Bands(20)  — statistical extreme confirmation
#     6. ATR(14)              — dynamic TP/SL sizing per coin
#
#   BUY requires ALL 4 core conditions:
#     RSI < 30  AND  EMA9 > EMA21  AND  volume spike  AND  MACD improving
#
#   ATR sets TP = 2× ATR, SL = 1× ATR (always 2:1 reward/risk ratio)
# =============================================================


# ── 1. RSI (Wilder's Smoothing) ───────────────────────────────
def calculate_rsi(closes, period=14):
    """
    Relative Strength Index using Wilder's exponential smoothing.
    Returns float 0-100, or None if not enough data.

    Oversold = below 30 (price fell hard, statistically likely to bounce).
    Overbought = above 70 (price rose hard, statistically likely to pull back).
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Seed with simple average of first `period` moves
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing for remaining candles
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── 2. EMA ────────────────────────────────────────────────────
def calculate_ema(closes, period):
    """
    Exponential Moving Average, seeded with SMA.
    k = 2 / (period + 1) — standard multiplier.
    Returns None if not enough data.
    """
    if len(closes) < period:
        return None

    k   = 2 / (period + 1)
    ema = sum(closes[:period]) / period

    for price in closes[period:]:
        ema = price * k + ema * (1 - k)

    return round(ema, 8)


# ── 3. MACD (12, 26, 9) ───────────────────────────────────────
def calculate_macd(closes, fast=12, slow=26, signal_period=9):
    """
    Moving Average Convergence Divergence.

    MACD line   = EMA(fast) - EMA(slow)
    Signal line = EMA(signal_period) of MACD line
    Histogram   = MACD line - Signal line

    Returns (macd_val, signal_val, histogram, prev_histogram).

    Key insight for trading:
      histogram rising (prev_hist < curr_hist) = selling pressure weakening
      This often precedes the price bounce by 1-2 candles.
    """
    # Need enough data to build MACD series + 2 signal values for prev/curr histogram
    if len(closes) < slow + signal_period + 2:
        return None, None, None, None

    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    k_sig  = 2 / (signal_period + 1)

    # Advance fast EMA from its seed (index fast-1) up to index slow-1
    ema_f = sum(closes[:fast]) / fast
    for p in closes[fast:slow]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)

    # Seed slow EMA at index slow-1
    ema_s = sum(closes[:slow]) / slow

    # Build MACD line from index slow onwards (one value per candle)
    macd_line = []
    for p in closes[slow:]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)
        ema_s = p * k_slow + ema_s * (1 - k_slow)
        macd_line.append(ema_f - ema_s)

    if len(macd_line) < signal_period + 2:
        return None, None, None, None

    # Build signal line (EMA of MACD line)
    sig_val = sum(macd_line[:signal_period]) / signal_period
    signal_line = [sig_val]
    for m in macd_line[signal_period:]:
        sig_val = m * k_sig + sig_val * (1 - k_sig)
        signal_line.append(sig_val)

    curr_macd = macd_line[-1]
    prev_macd = macd_line[-2]
    curr_sig  = signal_line[-1]
    prev_sig  = signal_line[-2]

    curr_hist = curr_macd - curr_sig
    prev_hist = prev_macd - prev_sig

    return round(curr_macd, 8), round(curr_sig, 8), round(curr_hist, 8), round(prev_hist, 8)


# ── 4. Volume Spike ───────────────────────────────────────────
def check_volume_spike(klines, multiplier=1.2, period=20):
    """
    Returns (is_spike: bool, ratio: float).

    Checks if the last CLOSED candle's volume exceeds multiplier × average
    of the prior `period` candles.

    Why this matters: price can be oversold but if nobody is buying,
    the bounce won't happen. A volume spike = real market participants
    stepping in. Without it, the bounce often fails.

    klines[-1] = current still-open candle (excluded)
    klines[-2] = last closed candle (the signal candle)
    """
    if len(klines) < period + 2:
        return False, 0.0

    current_vol = float(klines[-2][5])
    prior_vols  = [float(k[5]) for k in klines[-period - 2:-2]]
    avg_vol     = sum(prior_vols) / len(prior_vols) if prior_vols else 0

    if avg_vol == 0:
        return False, 0.0

    ratio = current_vol / avg_vol
    return ratio >= multiplier, round(ratio, 2)


# ── 5. Bollinger Bands ────────────────────────────────────────
def calculate_bollinger_bands(closes, period=20, num_std=2.0):
    """
    Bollinger Bands: middle = SMA(period), upper/lower = middle ± num_std × stddev.

    Price below lower band = statistically extreme oversold condition.
    This is an INDEPENDENT confirmation of RSI oversold — they measure
    different things (momentum vs. price distance from average).
    Having both RSI<30 AND price below lower BB is a very strong signal.
    """
    if len(closes) < period:
        return None, None, None

    recent   = closes[-period:]
    middle   = sum(recent) / period
    variance = sum((p - middle) ** 2 for p in recent) / period
    std_dev  = variance ** 0.5

    upper = middle + num_std * std_dev
    lower = middle - num_std * std_dev
    return round(upper, 8), round(middle, 8), round(lower, 8)


# ── 6. ATR (Average True Range) ───────────────────────────────
def calculate_atr(klines, period=14):
    """
    Average True Range using Wilder's smoothing.

    True Range = max of:
      - current high - current low
      - abs(current high - previous close)
      - abs(current low - previous close)

    ATR measures how much a coin moves per candle on average.
    Used to set TP and SL dynamically:
      - High ATR coin (volatile): wider TP and SL
      - Low ATR coin (stable):    tighter TP and SL

    This prevents the fixed-% approach from being too tight on volatile
    coins (constant SL hits) or too wide on stable ones (slow profits).
    """
    closed = klines[:-1]  # exclude current open candle
    if len(closed) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closed)):
        high       = float(closed[i][2])
        low        = float(closed[i][3])
        prev_close = float(closed[i - 1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Wilder's smoothing
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


# ── 7. Stochastic RSI ────────────────────────────────────────
def calculate_stoch_rsi(closes, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """
    Stochastic RSI — faster and more sensitive than plain RSI.

    Formula:
      1. Build RSI series over all closes
      2. Stoch = (RSI - lowest RSI in period) / (highest RSI - lowest RSI)
      3. %K = smoothed Stoch,  %D = smoothed %K

    StochRSI < 20 = strongly oversold (price likely to bounce very soon)
    StochRSI > 80 = strongly overbought

    Why better than RSI alone:
      RSI reacts to price. StochRSI reacts to RSI momentum — it catches
      turning points 1-3 candles BEFORE RSI crosses 30.
      Combined with RSI<30, it's extremely high confidence.

    Returns (k, d) both 0-100, or (None, None) if not enough data.
    """
    needed = rsi_period + stoch_period + smooth_k + smooth_d + 2
    if len(closes) < needed:
        return None, None

    # Build RSI series
    rsi_values = []
    for i in range(rsi_period, len(closes) + 1):
        r = calculate_rsi(closes[:i], rsi_period)
        if r is not None:
            rsi_values.append(r)

    if len(rsi_values) < stoch_period + smooth_k + smooth_d:
        return None, None

    # Stochastic of RSI
    raw_k = []
    for i in range(stoch_period, len(rsi_values) + 1):
        window    = rsi_values[i - stoch_period:i]
        low_rsi   = min(window)
        high_rsi  = max(window)
        denom     = high_rsi - low_rsi
        stoch     = ((rsi_values[i - 1] - low_rsi) / denom * 100) if denom > 0 else 50.0
        raw_k.append(stoch)

    if len(raw_k) < smooth_k + smooth_d:
        return None, None

    # Smooth %K
    k_series = []
    for i in range(smooth_k, len(raw_k) + 1):
        k_series.append(sum(raw_k[i - smooth_k:i]) / smooth_k)

    if len(k_series) < smooth_d:
        return None, None

    # Smooth %D
    d_series = []
    for i in range(smooth_d, len(k_series) + 1):
        d_series.append(sum(k_series[i - smooth_d:i]) / smooth_d)

    return round(k_series[-1], 2), round(d_series[-1], 2)


# ── 8. Hammer / Doji Candle Pattern ──────────────────────────
def check_candle_pattern(klines):
    """
    Detects bullish reversal candle patterns on the last closed candle.

    HAMMER: Small body at top, long lower wick (price rejected lower levels)
      - Lower wick >= 2x body size
      - Upper wick <= 0.5x body size
      - Signals: sellers tried to push price down, buyers overwhelmed them

    DOJI: Open ≈ Close (indecision — trend about to reverse)
      - Body size < 0.1% of price
      - Signals: neither buyers nor sellers in control — reversal likely

    Both patterns at oversold RSI = very high confidence bounce signal.

    Returns (pattern: str or None, is_bullish: bool)
    """
    try:
        closed = klines[:-1]  # exclude open candle
        if len(closed) < 2:
            return None, False

        c      = closed[-1]
        open_  = float(c[1])
        high   = float(c[2])
        low    = float(c[3])
        close  = float(c[4])

        body        = abs(close - open_)
        upper_wick  = high - max(open_, close)
        lower_wick  = min(open_, close) - low
        candle_size = high - low

        if candle_size == 0:
            return None, False

        body_pct = body / close * 100

        # Doji: body < 0.15% of price
        if body_pct < 0.15 and candle_size > 0:
            return "DOJI", True

        # Hammer: lower wick >= 2x body, upper wick small, close > open (green)
        if (body > 0
                and lower_wick >= 2.0 * body
                and upper_wick <= 0.5 * body
                and close >= open_):
            return "HAMMER", True

        # Bullish engulfing: current green candle body fully covers previous red candle
        prev      = closed[-2]
        prev_open = float(prev[1])
        prev_close= float(prev[4])
        if (close > open_                    # current is green
                and prev_close < prev_open   # previous is red
                and open_ <= prev_close      # current opens at or below prev close
                and close >= prev_open):     # current closes at or above prev open
            return "ENGULFING", True

        return None, False
    except Exception:
        return None, False


# ── 9. Whale Trade Detection ─────────────────────────────────
def check_whale_trades(client, symbol, min_usdt=20000, lookback=50):
    """
    Checks recent trades for large BUY orders (whale activity).

    Fetches last `lookback` trades and checks if large trades
    (each worth > min_usdt) are majority BUY side.

    Why this matters:
      Whales move markets. If a wallet spent $20k+ buying this coin
      in the last few minutes, they know something — or will cause
      the price to move up just by the size of their order.
      Following whale buys is one of the most reliable signals.

    Returns (is_whale_buying: bool, buy_pct: float)
    """
    try:
        trades   = client.get_recent_trades(symbol=symbol, limit=lookback)
        large    = [t for t in trades if float(t['qty']) * float(t['price']) >= min_usdt]
        if not large:
            return True, 50.0  # no whale trades — don't block signal
        buys     = sum(1 for t in large if not t['isBuyerMaker'])
        buy_pct  = (buys / len(large)) * 100
        return buy_pct >= 50.0, round(buy_pct, 1)
    except Exception:
        return True, 50.0  # don't block on API error


# ── 8. RSI Divergence ────────────────────────────────────────
def check_rsi_divergence(closes, rsi_period=14, lookback=20):
    """
    Bullish RSI Divergence: price makes a lower low but RSI makes a higher low.

    This means selling momentum is WEAKENING even as price drops.
    It's one of the strongest reversal signals in technical analysis.

    How it works:
      - Find the lowest price in the last `lookback` candles (point A)
      - Find the second lowest price before that (point B, earlier)
      - If price_A < price_B (lower low) BUT rsi_A > rsi_B (higher RSI)
        → divergence confirmed → bounce is likely

    Returns (divergence: bool, description: str)
    """
    if len(closes) < lookback + rsi_period:
        return False, "insufficient_data"

    # Build RSI series for last `lookback` closes
    rsi_series = []
    for i in range(lookback):
        window = closes[-(lookback - i + rsi_period): -(lookback - i) or None]
        r = calculate_rsi(window, rsi_period)
        rsi_series.append(r if r is not None else 50.0)

    prices = closes[-lookback:]

    # Find two lowest price points
    min1_idx = prices.index(min(prices))
    # Find second minimum in first half (earlier in time)
    first_half = prices[:max(1, min1_idx)]
    if not first_half:
        return False, "no_second_low"

    min2_idx = first_half.index(min(first_half))

    price_low1 = prices[min1_idx]   # more recent low
    price_low2 = prices[min2_idx]   # earlier low
    rsi_low1   = rsi_series[min1_idx]
    rsi_low2   = rsi_series[min2_idx]

    # Bullish divergence: price lower low + RSI higher low
    if price_low1 < price_low2 and rsi_low1 > rsi_low2 + 2:
        return True, f"DIVERGENCE(P:{price_low2:.4f}->{price_low1:.4f} RSI:{rsi_low2:.1f}->{rsi_low1:.1f})"

    return False, "no_divergence"


# ── 9. Order Book Depth ───────────────────────────────────────
def check_order_book(client, symbol, depth=20):
    """
    Checks bid vs ask pressure in the order book.

    Fetches top `depth` bid and ask levels.
    bid_volume / ask_volume > 1.3 means buyers are stronger than sellers.

    Why this matters:
      RSI and MACD are based on past prices.
      Order book shows LIVE intent — real money waiting to buy RIGHT NOW.
      A strong bid wall means price is unlikely to drop further.

    Returns (is_bullish: bool, ratio: float)
    """
    try:
        book      = client.get_order_book(symbol=symbol, limit=depth)
        bid_vol   = sum(float(b[1]) for b in book['bids'])
        ask_vol   = sum(float(a[1]) for a in book['asks'])
        if ask_vol == 0:
            return False, 0.0
        ratio = bid_vol / ask_vol
        return ratio >= 1.3, round(ratio, 2)
    except Exception:
        return True, 1.0  # don't block signal on API error


# ── MASTER SIGNAL FUNCTION ────────────────────────────────────
def get_signal(client, symbol, interval,
               rsi_period=14, ema_fast=9, ema_slow=21, rsi_oversold=30,
               volume_multiplier=1.2, bb_period=20):
    """
    Multi-timeframe signal engine (8 indicators):

    TREND   (4h candles): EMA9 > EMA21  — long-term uptrend?
    TREND   (1h candles): EMA9 > EMA21  — medium-term uptrend?
    ENTRY   (5m candles): RSI < 30       — short-term oversold dip?
    CONFIRM (5m candles): Volume spike + MACD turning up
    DEPTH   (live book) : Bids > Asks    — real buyers waiting now?
    """
    # ── 5m candles for RSI, MACD, Volume, BB, ATR ────────────
    klines = client.get_klines(symbol=symbol, interval=interval, limit=200)
    closes = [float(k[4]) for k in klines][:-1]

    # ── 1h candles for trend EMA ──────────────────────────────
    klines_1h  = client.get_klines(symbol=symbol, interval="1h", limit=50)
    closes_1h  = [float(k[4]) for k in klines_1h][:-1]
    ema_f_1h   = calculate_ema(closes_1h, ema_fast)
    ema_s_1h   = calculate_ema(closes_1h, ema_slow)

    # ── 4h candles for higher timeframe trend ─────────────────
    klines_4h  = client.get_klines(symbol=symbol, interval="4h", limit=50)
    closes_4h  = [float(k[4]) for k in klines_4h][:-1]
    ema_f_4h   = calculate_ema(closes_4h, ema_fast)
    ema_s_4h   = calculate_ema(closes_4h, ema_slow)

    # ── 5m indicators ─────────────────────────────────────────
    rsi = calculate_rsi(closes, rsi_period)

    # 5m EMAs for display only
    ema_f = calculate_ema(closes, ema_fast)
    ema_s = calculate_ema(closes, ema_slow)

    macd_val, sig_val, macd_hist, prev_hist = calculate_macd(closes)
    vol_spike, vol_ratio = check_volume_spike(klines, multiplier=volume_multiplier)

    bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(closes, period=bb_period)
    current_price = closes[-1]
    at_bb_lower   = bb_lower is not None and current_price <= bb_lower

    atr = calculate_atr(klines)

    # ── Order book depth (live bid/ask pressure) ──────────────
    ob_bullish, ob_ratio = check_order_book(client, symbol)

    # ── Whale trade detection ──────────────────────────────────
    whale_buying, whale_buy_pct = check_whale_trades(client, symbol)

    # ── RSI Divergence ─────────────────────────────────────────
    divergence, div_desc = check_rsi_divergence(closes, rsi_period)

    # ── Stochastic RSI ─────────────────────────────────────────
    stoch_k, stoch_d = calculate_stoch_rsi(closes, rsi_period)
    cond_stoch = (stoch_k is not None and stoch_k < 20)  # strongly oversold

    # ── Candle pattern ─────────────────────────────────────────
    candle_pattern, candle_bullish = check_candle_pattern(klines)

    # ── Dynamic TP/SL from ATR ────────────────────────────────
    if atr is not None and current_price > 0:
        atr_pct = (atr / current_price) * 100
        # Keep minimum floors above fee drag to avoid "small-win but net-loss" behavior.
        tp_pct  = round(max(2.5, min(5.0, atr_pct * 2.0)), 2)
        sl_pct  = round(max(1.5, min(2.5, atr_pct * 1.0)), 2)
    else:
        tp_pct = 2.5
        sl_pct = 1.5

    # ── Fallback: not enough data ─────────────────────────────
    if rsi is None or ema_f_1h is None or ema_s_1h is None:
        return _result("HOLD", rsi, ema_f, ema_s, macd_hist, prev_hist,
                       vol_ratio, at_bb_lower, tp_pct, sl_pct, ob_ratio, reason="INSUFFICIENT_DATA")

    # ── Core conditions ───────────────────────────────────────
    cond_4h_trend = (ema_f_4h is not None and ema_s_4h is not None
                     and ema_f_4h > ema_s_4h)           # 4h uptrend
    cond_trend    = ema_f_1h > ema_s_1h                 # 1h uptrend
    cond_rsi      = rsi < rsi_oversold                  # 5m oversold
    cond_volume   = vol_spike                            # real buying interest
    cond_macd     = (macd_hist is not None
                     and prev_hist is not None
                     and macd_hist > prev_hist)          # momentum turning up
    cond_ob       = ob_bullish                           # bids > asks (live buyers)
    cond_whale    = whale_buying                         # whales buying this coin
    # ── Core: must ALL pass ───────────────────────────────────
    # These 6 are non-negotiable — without them, signal is weak
    all_core = (cond_4h_trend and cond_trend and cond_rsi
                and cond_volume and cond_macd and cond_ob)

    # ── Bonus confirmations (not required, but counted) ───────
    # StochRSI and Whale are bonus — they increase confidence
    # but alone shouldn't block a good trade
    bonus_stoch = cond_stoch                   # StochRSI < 20
    bonus_whale = cond_whale                   # whale majority buying
    bonus_diverg = divergence                  # RSI divergence
    bonus_candle = candle_bullish              # hammer/doji/engulfing
    bonus_bb     = at_bb_lower                # price at BB lower band
    bonus_macd_p = (macd_hist is not None and macd_hist > 0)  # MACD positive
    bonus_5m_ema = (ema_f is not None and ema_s is not None and ema_f > ema_s)

    signal = "BUY" if all_core else "HOLD"

    if signal == "HOLD":
        failed = []
        if not cond_rsi:      failed.append(f"RSI={rsi:.1f}(need<{rsi_oversold})")
        if not cond_4h_trend: failed.append("4h_downtrend")
        if not cond_trend:    failed.append(f"1h_EMA{ema_fast}<EMA{ema_slow}")
        if not cond_volume:   failed.append(f"Vol={vol_ratio:.1f}x(need>{volume_multiplier}x)")
        if not cond_macd:     failed.append("MACD_not_rising")
        if not cond_ob:       failed.append(f"OB={ob_ratio:.2f}(asks>bids)")
        reason = " | ".join(failed)
    else:
        # Count bonus score — shown in reason so you know signal strength
        bonuses = []
        if bonus_stoch:  bonuses.append(f"StochRSI={stoch_k}")
        if bonus_whale:  bonuses.append(f"Whale={whale_buy_pct:.0f}%buys")
        if bonus_diverg: bonuses.append(div_desc)
        if bonus_candle: bonuses.append(candle_pattern)
        if bonus_bb:     bonuses.append("BB_touch")
        if bonus_macd_p: bonuses.append("MACD_positive")
        if bonus_5m_ema: bonuses.append("5m_EMA_aligned")
        score = len(bonuses)
        reason = f"ALL_CONFIRMED [score:{score}/7]" + (f" +{','.join(bonuses)}" if bonuses else "")

    return _result(signal, rsi, ema_f_1h, ema_s_1h, macd_hist, prev_hist,
                   vol_ratio, at_bb_lower, tp_pct, sl_pct, ob_ratio, reason)


def _result(signal, rsi, ema_f, ema_s, macd_hist, prev_hist,
            vol_ratio, at_bb_lower, tp_pct, sl_pct, ob_ratio=1.0, reason=""):
    """Build a consistent result dict for all return paths."""
    return {
        "signal":      signal,
        "rsi":         rsi,
        "ema_f":       ema_f,
        "ema_s":       ema_s,
        "macd_hist":   macd_hist,
        "prev_hist":   prev_hist,
        "vol_ratio":   vol_ratio,
        "at_bb_lower": at_bb_lower,
        "tp_pct":      tp_pct,
        "sl_pct":      sl_pct,
        "ob_ratio":    ob_ratio,
        "reason":      reason,
    }
