# =============================================================
#   SCREENER — Scans the market for the best trading pairs
# =============================================================

def get_top_usdt_pairs(client, limit=50):
    """
    Fetch all USDT pairs, filter by volume, volatility, and blacklist.
    Returns the top `limit` symbols by 24h USDT volume.
    """
    tickers = client.get_ticker()

    import config
    blacklist = getattr(config, "COIN_BLACKLIST", [])

    avoid_keywords = [
        'USDC', 'FDUSD', 'TUSD', 'DAI', 'USDP', 'BUSD',
        'EUR', 'GBP', 'TRY', 'BRL', 'RUB', 'ZAR', 'UAH', 'NGN',
        'PAXG', 'XAUT'
    ]

    usdt_pairs = []
    for t in tickers:
        try:
            symbol = t['symbol']

            if not symbol.endswith('USDT'):
                continue
            if symbol in blacklist:
                continue
            if 'UP' in symbol or 'DOWN' in symbol:
                continue

            is_stable_or_fiat = any(x in symbol for x in avoid_keywords)
            if is_stable_or_fiat:
                continue

            price = float(t.get('lastPrice', 0) or 0)
            if price < getattr(config, "MIN_PRICE", 1.0):
                continue

            # Skip pairs with <0.1% 24h move — true stablecoins only
            if abs(float(t.get('priceChangePercent', 0) or 0)) < 0.1:
                continue

            # Skip coins with no active market (bid=0 or ask=0 = delisted/illiquid)
            bid = float(t.get('bidPrice', 0) or 0)
            ask = float(t.get('askPrice', 0) or 0)
            if bid == 0 or ask == 0:
                continue

            # Minimum $200K daily volume — real coins with active markets
            vol = float(t.get('quoteVolume', 0) or 0)
            if vol < 200_000:
                continue

            usdt_pairs.append({'symbol': symbol, 'volume': vol})
        except Exception:
            continue

    usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
    return [p['symbol'] for p in usdt_pairs[:limit]]


def check_btc_market(client, interval):
    """
    Returns True if it's safe to look for BUY signals.

    BTC is the market anchor — when BTC is in a hard downtrend,
    altcoins fall with it. Even if an altcoin RSI hits 30, it usually
    keeps falling because BTC is dragging the whole market down.

    Safe to trade when BTC 1h RSI is between 35-70:
      - RSI < 35: BTC itself is oversold/crashing — too risky for alts
      - RSI > 70: BTC is overbought, alts are pumped — chasing highs
      - RSI 35-70: neutral/recovering — altcoin dip-buys work well
    """
    from strategy import calculate_rsi, calculate_ema
    try:
        klines = client.get_klines(symbol="BTCUSDT", interval="1h", limit=50)
        closes = [float(k[4]) for k in klines][:-1]
        btc_rsi = calculate_rsi(closes, 14)
        btc_ema9  = calculate_ema(closes, 9)
        btc_ema21 = calculate_ema(closes, 21)

        if btc_rsi is None:
            return True, "BTC_DATA_UNAVAILABLE"

        # Hard crash: BTC RSI below 25 — alts will follow down hard
        if btc_rsi < 25:
            return False, f"BTC_CRASHING RSI={btc_rsi:.1f} — alts will follow down"

        # Extreme overbought: parabolic, alts top out
        if btc_rsi > 80:
            return False, f"BTC_PARABOLIC RSI={btc_rsi:.1f} — too hot, wait for pullback"

        # BTC downtrend: only block if RSI is also very low (both falling hard)
        # RSI 30-45 with downtrend = altcoins can still bounce if deeply oversold
        if btc_ema9 is not None and btc_ema21 is not None and btc_ema9 < btc_ema21:
            if btc_rsi < 30:
                return False, f"BTC_DOWNTREND RSI={btc_rsi:.1f} EMA9<EMA21 — waiting"

        # Bull market signal
        if btc_rsi >= 60 and btc_ema9 is not None and btc_ema9 > btc_ema21:
            return True, f"BTC_BULL RSI={btc_rsi:.1f} — bull market, alt dips are opportunities"

        return True, f"BTC_OK RSI={btc_rsi:.1f}"
    except Exception as e:
        return True, f"BTC_CHECK_FAILED({e})"  # don't block on error


def find_best_entry(client, symbols, interval):
    """
    Scan symbols and return BUY-signal candidates sorted by lowest RSI.
    Each candidate is the full signal dict from get_signal().
    """
    from strategy import get_signal
    import config

    # Check BTC market health before scanning altcoins
    market_ok, market_reason = check_btc_market(client, interval)
    if not market_ok:
        print(f"   [Market Filter] Skipping scan: {market_reason}", flush=True)
        return []

    print(f"   [Market Filter] {market_reason}", flush=True)

    candidates  = []
    api_errors  = 0
    MAX_ERRORS  = 5   # if 5 consecutive API errors, pause 10s (rate limit likely)
    print(f"   [Screener] Scanning {len(symbols)} coins...", flush=True)

    for i, symbol in enumerate(symbols):
        # Small delay every 10 coins to avoid hitting Binance rate limits
        # Each coin makes ~5 API calls; 50 coins = ~250 calls. Binance limit = 1200/min.
        # 0.08s delay = 50 coins × 5 calls × 0.08s ≈ 20s total scan — safe margin.
        if i > 0 and i % 8 == 0:
            import time as _time
            _time.sleep(1.5)

        try:
            data = get_signal(
                client, symbol, interval,
                rsi_period        = config.RSI_PERIOD,
                ema_fast          = config.EMA_FAST,
                ema_slow          = config.EMA_SLOW,
                rsi_oversold      = config.RSI_OVERSOLD,
                volume_multiplier = getattr(config, "VOLUME_MULTIPLIER", 1.2),
                bb_period         = getattr(config, "BB_PERIOD", 20),
            )
            data['symbol'] = symbol
            api_errors = 0  # reset on success

            if data['rsi'] is not None:
                candidates.append(data)

        except Exception as e:
            api_errors += 1
            err_str = str(e).lower()
            # Rate limit hit — pause and continue
            if "429" in err_str or "418" in err_str or api_errors >= MAX_ERRORS:
                print(f"   [Screener] Rate limit / repeated errors — pausing 15s (errors={api_errors})", flush=True)
                import time as _time
                _time.sleep(15)
                api_errors = 0
            continue  # skip this coin, move to next

    # Only return confirmed BUY signals, sorted by lowest RSI (most oversold first)
    buy_candidates = [c for c in candidates if c['signal'] == 'BUY']
    buy_candidates.sort(key=lambda x: x['rsi'])

    # Always show the 3 closest candidates so user knows how far we are
    if not buy_candidates and candidates:
        closest = sorted(candidates, key=lambda x: x['rsi'])[:3]
        print("   [Screener] No BUY yet — closest candidates:", flush=True)
        for c in closest:
            rsi_str  = f"{c['rsi']:.1f}" if c['rsi'] is not None else "N/A"
            vol_str  = f"{c['vol_ratio']:.1f}x"
            print(f"      {c['symbol']:12s} RSI={rsi_str:5s} Vol={vol_str:5s} | {c['reason']}", flush=True)

    return buy_candidates
