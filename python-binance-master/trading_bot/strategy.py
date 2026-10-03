# =============================================================
#   STRATEGY — four non-overlapping checks
#
#     1. Daily EMA 50 / 200     — trend direction
#     2. 4h Fibonacci 0.50–0.618 — pullback location
#     3. 4h RSI divergence      — momentum turning
#     4. 4h volume spike        — the move has participation
#
#   A buy requires all four. Stop sits beyond the swing low.
#   Target is the prior swing high, and only if it pays >= 1.5x the stop.
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
            return False, 0.0  # no large trades — not evidence of buying
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

    prices = closes[-lookback:]
    # Calculate each RSI against all price history available at that candle.
    # Slicing only `rsi_period` prices supplies 14 closes to RSI(14), which
    # needs 15 and silently turned every value into the 50.0 fallback.
    start = len(closes) - lookback
    rsi_series = [
        calculate_rsi(closes[:start + index + 1], rsi_period)
        for index in range(lookback)
    ]
    if any(value is None for value in rsi_series):
        return False, "insufficient_RSI_history"

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

    Returns (is_bullish: bool, ratio: float, spread_pct: float)
    """
    try:
        book      = client.get_order_book(symbol=symbol, limit=depth)
        bid_vol   = sum(float(b[1]) for b in book['bids'])
        ask_vol   = sum(float(a[1]) for a in book['asks'])
        best_bid  = float(book['bids'][0][0]) if book['bids'] else 0.0
        best_ask  = float(book['asks'][0][0]) if book['asks'] else 0.0
        spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid > 0 else 99.0
        if ask_vol == 0:
            return False, 0.0, round(spread_pct, 3)
        ratio = bid_vol / ask_vol
        return ratio >= 1.3, round(ratio, 2), round(spread_pct, 3)
    except Exception:
        # A missing order book cannot confirm liquidity or bid support.
        return False, 0.0, 99.0


def check_bounce_started(klines):
    """
    True only after sellers have already lost the last closed candle.

    A green candle that closes in its upper half, or a hammer / bullish
    engulfing, means the dip has started to reverse. Buying the red
    candle itself is how this bot kept catching falling knives.
    """
    closed = klines[:-1]
    if len(closed) < 2:
        return False

    candle = closed[-1]
    open_ = float(candle[1])
    high = float(candle[2])
    low = float(candle[3])
    close = float(candle[4])
    if high <= low:
        return False

    green_upper_half = close > open_ and close >= (high + low) / 2
    pattern, _bullish = check_candle_pattern(klines)
    return green_upper_half or pattern in ("HAMMER", "ENGULFING")


def calculate_adx(klines, period=14):
    """
    Trend strength plus direction.
    Returns (adx, plus_di, minus_di), or None.
    ADX above 20 means a trend exists. Plus DI above minus DI means that trend is up.
    """
    closed = klines[:-1]
    if len(closed) < period * 2 + 1:
        return None

    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closed)):
        high = float(closed[i][2])
        low = float(closed[i][3])
        prev_high = float(closed[i - 1][2])
        prev_low = float(closed[i - 1][3])
        prev_close = float(closed[i - 1][4])
        up = high - prev_high
        down = prev_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    def _wilder(values):
        total = sum(values[:period])
        out = [total]
        for value in values[period:]:
            total = total - (total / period) + value
            out.append(total)
        return out

    dxs = []
    last_plus_di = None
    last_minus_di = None
    for atr, plus, minus in zip(_wilder(trs), _wilder(plus_dm), _wilder(minus_dm)):
        if atr == 0:
            continue
        plus_di = 100 * plus / atr
        minus_di = 100 * minus / atr
        last_plus_di = plus_di
        last_minus_di = minus_di
        denom = plus_di + minus_di
        dxs.append(0.0 if denom == 0 else 100 * abs(plus_di - minus_di) / denom)

    if len(dxs) < period or last_plus_di is None:
        return None
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return round(adx, 2), round(last_plus_di, 2), round(last_minus_di, 2)


def obv_is_rising(klines, lookback=5):
    """
    On-balance volume. Rising OBV means closes are being confirmed by volume,
    which is the volume check in place of a raw spike.
    """
    closed = klines[:-1]
    if len(closed) < lookback + 2:
        return False

    obv = 0.0
    series = []
    prev_close = float(closed[0][4])
    for candle in closed[1:]:
        close = float(candle[4])
        volume = float(candle[5])
        if close > prev_close:
            obv += volume
        elif close < prev_close:
            obv -= volume
        series.append(obv)
        prev_close = close

    return len(series) > lookback and series[-1] > series[-1 - lookback]


def find_golden_zone(klines, lookback=80):
    """
    Fibonacci pullback of the last meaningful rally on this timeframe.

    Golden zone = price has retraced 50% to 61.8% of the move from the
    swing low up to the swing high. That is the pullback area the blueprint
    uses. A rally smaller than 8% is ignored because the zone is just noise.
    """
    closed = klines[:-1]
    if len(closed) < 30:
        return None
    window = closed[-min(lookback, len(closed)):]
    highs = [float(k[2]) for k in window]
    lows = [float(k[3]) for k in window]
    high_idx = highs.index(max(highs))
    if high_idx < 5:
        return None

    swing_high = highs[high_idx]
    swing_low = min(lows[:high_idx])
    if swing_low <= 0 or swing_high <= swing_low:
        return None
    if (swing_high - swing_low) / swing_low < 0.08:
        return None

    price = float(klines[-1][4])
    span = swing_high - swing_low
    retrace = (swing_high - price) / span
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "retrace": round(retrace, 3),
        "in_zone": 0.50 <= retrace <= 0.618,
        "price": price,
    }


# ── MASTER SIGNAL FUNCTION ────────────────────────────────────
def get_signal(client, symbol, interval,
               rsi_period=14, ema_fast=50, ema_slow=200, rsi_oversold=30,
               volume_multiplier=1.3, bb_period=20):
    """
    Seven configured checks. A buy requires every one:

      Trend     — daily EMA50 above EMA200, price above EMA200, ADX/+DI up
      Level     — 4h price sitting in the Fibonacci 0.50–0.618 pullback
      RSI       — bullish divergence and a non-crash oversold RSI range
      MACD      — histogram improving
      Bands     — price at the lower Bollinger band
      Volume    — OBV rising, reversal candle, and 1.3x volume spike

    Stop goes just beyond the swing low. Target is the swing high.
    The trade is skipped when that target pays less than 1.5x the stop.
    """
    import config as _config

    daily = client.get_klines(symbol=symbol, interval="1d", limit=250)
    daily_closes = [float(k[4]) for k in daily][:-1]
    ema50 = calculate_ema(daily_closes, ema_fast)
    ema200 = calculate_ema(daily_closes, ema_slow)
    daily_price = daily_closes[-1] if daily_closes else None

    entry_interval = interval or "4h"
    klines_4h = client.get_klines(symbol=symbol, interval=entry_interval, limit=200)
    closes_4h = [float(k[4]) for k in klines_4h][:-1]
    rsi = calculate_rsi(closes_4h, rsi_period)
    divergence, div_desc = check_rsi_divergence(closes_4h, rsi_period)
    vol_spike, vol_ratio = check_volume_spike(klines_4h, multiplier=volume_multiplier)
    macd_val, _macd_signal, macd_hist, prev_hist = calculate_macd(
        closes_4h,
        getattr(_config, "MACD_FAST", 12),
        getattr(_config, "MACD_SLOW", 26),
        getattr(_config, "MACD_SIGNAL_PERIOD", 9),
    )
    bb_upper, _bb_mid, bb_lower = calculate_bollinger_bands(
        closes_4h,
        getattr(_config, "BB_PERIOD", bb_period),
        getattr(_config, "BB_STD_DEV", 2.0),
    )
    zone = find_golden_zone(klines_4h)
    adx_pack = calculate_adx(klines_4h, getattr(_config, "ADX_PERIOD", 14))
    if adx_pack is None:
        adx, plus_di, minus_di = None, None, None
    else:
        adx, plus_di, minus_di = adx_pack
    adx_min = getattr(_config, "ADX_MIN", 20)
    obv_rising = obv_is_rising(klines_4h)
    atr = calculate_atr(klines_4h)

    _bullish_book, ob_ratio, spread_pct = check_order_book(client, symbol)
    max_spread = getattr(_config, "MAX_SPREAD_PCT", 0.25)
    min_rr = getattr(_config, "MIN_REWARD_RISK", 1.5)

    bounce_confirmed = check_bounce_started(klines_4h)

    cond_ema = (
        ema50 is not None and ema200 is not None and daily_price is not None
        and ema50 > ema200 and daily_price > ema200
    )
    cond_adx = (
        adx is not None and plus_di is not None and minus_di is not None
        and adx >= adx_min and plus_di > minus_di
    )
    cond_trend = cond_ema and cond_adx
    cond_zone = zone is not None and zone["in_zone"]
    rsi_floor = getattr(_config, "RSI_OVERSOLD_MIN", 20)
    cond_rsi = rsi is not None and rsi_floor <= rsi < rsi_oversold
    cond_div = bool(divergence)
    cond_macd = (
        macd_hist is not None and prev_hist is not None
        and macd_hist > prev_hist
    )
    current_price = closes_4h[-1] if closes_4h else 0.0
    cond_bb = bb_lower is not None and current_price <= bb_lower
    cond_volume = obv_rising and bounce_confirmed and vol_spike
    cond_spread = spread_pct <= max_spread

    tp_pct = float(getattr(_config, "TAKE_PROFIT_PCT", 4.5))
    max_sl = abs(float(getattr(_config, "STOP_LOSS_PCT", -2.0)))
    sl_pct = max_sl
    fee_pct = float(getattr(_config, "TAKER_FEE_RATE", 0.001)) * 2 * 100
    rr_ok = False
    if zone is not None and zone["price"] > 0:
        swing_risk = zone["price"] - zone["swing_low"]
        atr_risk = atr if atr else 0.0
        risk = max(swing_risk, atr_risk)
        reward = zone["swing_high"] - zone["price"]
        if risk > 0 and reward > 0:
            sl_pct = round(risk / zone["price"] * 100, 2)
            tp_pct = round(reward / zone["price"] * 100, 2)
            net_tp = tp_pct - fee_pct
            # A stop wider than the config cap is skipped. Clamping it to 2%
            # would sit inside the swing and get hit by normal noise.
            rr_ok = (net_tp / sl_pct) >= min_rr and 1.5 <= sl_pct <= max_sl

    conditions = (
        cond_trend, cond_zone, cond_rsi, cond_div, cond_macd, cond_bb, cond_volume,
    )
    score = sum(conditions)
    required_score = getattr(_config, "MIN_CONFIRMATIONS", len(conditions))
    signal = "BUY" if score == len(conditions) and score >= required_score and cond_spread and rr_ok else "HOLD"

    if signal == "BUY":
        retrace = zone["retrace"]
        adx_txt = f"{adx:.1f}" if adx is not None else "N/A"
        reason = (
            f"EMA+ADX+ZONE+RSI+MACD+BB+VOLUME [score:{score}/{len(conditions)}] "
            f"ADX={adx_txt} fib={retrace:.2f} {div_desc}"
        )
    else:
        failed = []
        if not cond_ema:
            failed.append("daily_EMA50<=EMA200")
        elif not cond_adx:
            adx_txt = f"{adx:.1f}" if adx is not None else "N/A"
            if adx is None or adx < adx_min:
                failed.append(f"ADX={adx_txt}(need>={adx_min})")
            else:
                failed.append(f"DI_down +{plus_di:.0f}/-{minus_di:.0f}")
        if zone is None:
            failed.append("no_swing")
        elif not cond_zone:
            failed.append(f"fib={zone['retrace']:.2f}(need 0.50-0.62)")
        if not cond_rsi:
            rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
            failed.append(f"RSI={rsi_txt}(need {rsi_floor}-{rsi_oversold})")
        if not cond_div:
            failed.append("no_RSI_divergence")
        if not cond_macd:
            failed.append("MACD_not_rising")
        if not cond_bb:
            failed.append("not_at_BB_lower")
        if not obv_rising:
            failed.append("OBV_falling")
        elif not bounce_confirmed:
            failed.append("no_reversal_candle")
        elif not vol_spike:
            failed.append(f"Vol={vol_ratio:.1f}x(need>{volume_multiplier}x)")
        if not cond_spread:
            failed.append(f"spread={spread_pct:.2f}%")
        if score == len(conditions) and cond_spread and not rr_ok:
            if sl_pct > max_sl:
                failed.append(f"stop {sl_pct:.1f}%>{max_sl:.1f}%")
            else:
                failed.append(f"reward/risk {tp_pct:.1f}/{sl_pct:.1f}")
        reason = " | ".join(failed) if failed else "HOLD"

    return _result(
        signal, rsi, ema50, ema200, macd_hist, prev_hist,
        vol_ratio, cond_bb, tp_pct, sl_pct, ob_ratio, reason, score,
    )


def _result(signal, rsi, ema_f, ema_s, macd_hist, prev_hist,
            vol_ratio, at_bb_lower, tp_pct, sl_pct, ob_ratio=1.0, reason="", score=0):
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
        "score":       score,
    }
