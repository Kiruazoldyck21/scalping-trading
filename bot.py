import ccxt
import pandas as pd
import requests
import time
import os
import traceback

# ────────────────────────────────────────────────
#               CONFIGURATION
# ────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("ERROR: Telegram BOT_TOKEN and CHAT_ID must be set in environment variables")
    exit(1)

CAPITAL      = 1000.0       # USDT
RISK_PERCENT = 0.8          # % of capital to risk per trade

TIMEFRAMES = {
    "bias":  "15m",
    "setup": "5m",
    "entry": "1m"
}

SLEEP_BETWEEN_PAIRS = 10.0      # seconds — be very conservative on Railway
MAX_PAIRS_TO_SCAN   = 50        # start small, increase later if stable

# ────────────────────────────────────────────────
#               EXCHANGE SETUP
# ────────────────────────────────────────────────

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# ────────────────────────────────────────────────
#               TELEGRAM SENDER
# ────────────────────────────────────────────────

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram send failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Telegram exception: {e}")

# ────────────────────────────────────────────────
#               PAIR LOADING
# ────────────────────────────────────────────────

def get_usdt_pairs(max_pairs: int = MAX_PAIRS_TO_SCAN) -> list[str]:
    try:
        markets = exchange.load_markets()
        pairs = [
            sym for sym in markets
            if sym.endswith("/USDT")
            and markets[sym].get('active', False)
            and "BUSD" not in sym
            and "USDC" not in sym
        ]
        # You can sort by quoteVolume later if desired
        return pairs[:max_pairs]
    except Exception as e:
        print(f"Failed to load markets: {e}")
        return []

SYMBOLS = get_usdt_pairs()

# ────────────────────────────────────────────────
#               DATA FETCH
# ────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame | None:
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"fetch_ohlcv failed {symbol} {timeframe}: {e}")
        return None

# ────────────────────────────────────────────────
#               INDICATORS
# ────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame) -> str | None:
    if df is None or len(df) < 8:
        return None

    lookback = 7
    recent_high = df['high'].iloc[-lookback-1:-1].max()
    recent_low  = df['low'].iloc[-lookback-1:-1].min()

    candle = df.iloc[-1]

    # Wick above recent high + close below it → swept buy-side liquidity
    if candle['high'] > recent_high and candle['close'] < recent_high:
        return "SELL"

    # Wick below recent low + close above it → swept sell-side liquidity
    if candle['low'] < recent_low and candle['close'] > recent_low:
        return "BUY"

    return None


def in_fib_zone(high: float, low: float, price: float) -> bool:
    if high <= low:
        return False
    rng = high - low
    for ratio in [0.618, 0.705, 0.786]:
        level = high - rng * ratio
        if abs(price - level) <= rng * 0.012:          # \~1.2% of range — quite forgiving
            return True
    return False


def has_good_volume(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 25:
        return False
    avg_vol = df['volume'].rolling(20).mean().iloc[-1]
    return df['volume'].iloc[-1] > avg_vol * 1.35


# ────────────────────────────────────────────────
#               MAIN ANALYSIS
# ────────────────────────────────────────────────

def scan_symbol(symbol: str):
    try:
        df15 = fetch_ohlcv(symbol, TIMEFRAMES["bias"])
        df5  = fetch_ohlcv(symbol, TIMEFRAMES["setup"])
        df1  = fetch_ohlcv(symbol, TIMEFRAMES["entry"], limit=120)

        if any(df is None for df in (df15, df5, df1)):
            return

        direction = detect_liquidity_sweep(df5)
        if not direction:
            return

        price = float(df1['close'].iloc[-1])

        if not has_good_volume(df1):
            return

        range_high = df5['high'].max()
        range_low  = df5['low'].min()

        if not in_fib_zone(range_high, range_low, price):
            return

        # ─── Risk & Targets ────────────────────────────────
        if direction == "SELL":
            sl   = price * 1.0045
            risk = sl - price
            tp1  = price - risk * 1.6
            tp2  = price - risk * 3.0
        else:  # BUY
            sl   = price * 0.9955
            risk = price - sl
            tp1  = price + risk * 1.6
            tp2  = price + risk * 3.0

        # Position size
        risk_usdt = CAPITAL * (RISK_PERCENT / 100)
        qty_approx = risk_usdt / risk if risk > 0 else 0

        emoji = "🟢" if direction == "BUY" else "🔴"
        title = "BUY" if direction == "BUY" else "SELL"

        bias_15m = "BULLISH" if df15['close'].iloc[-1] > df15['open'].iloc[-1] else "BEARISH"

        message = f"""**{emoji} {title} SIGNAL**  
**{symbol}**

**15m Bias** • {bias_15m}  
**Direction** • {direction}

**Entry** • `{price:.6f}`  
**Stop**  • `{sl:.6f}`  
**TP1**   • `{tp1:.6f}`  
**TP2**   • `{tp2:.6f}`

**Risk** • {risk_usdt:.2f} USDT  ({RISK_PERCENT}%)  
**Size** ≈ **{qty_approx:.2f}** {symbol.split('/')[0]}

_(scalp — 1m entry filter — fib confluence — volume spike)_
"""

        send_telegram(message)
        print(f"→ Alert sent: {symbol} {direction}")

    except Exception as e:
        print(f"Error scanning {symbol}: {e}")
        # traceback.print_exc()   # uncomment during debug


# ────────────────────────────────────────────────
#               MAIN LOOP
# ────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Bot started • {len(SYMBOLS)} pairs • Railway mode")
    send_telegram(f"Bot **started** on Railway\nScanning **{len(SYMBOLS)}** USDT pairs")

    while True:
        try:
            for symbol in SYMBOLS:
                scan_symbol(symbol)
                time.sleep(SLEEP_BETWEEN_PAIRS)

            # Reload pair list every \~6 hours
            time.sleep(60 * 60 * 6)
            SYMBOLS = get_usdt_pairs()
            print(f"Symbols refreshed → now {len(SYMBOLS)} pairs")

        except KeyboardInterrupt:
            print("Stopped manually")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            send_telegram(f"⚠️ Bot crashed: {str(e)[:180]}")
            time.sleep(300)  # 5 min cooldown
