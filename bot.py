import ccxt
import pandas as pd
import requests
import time
import os
import traceback

# ================= CONFIG =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = 1000
RISK_PERCENT = 0.8

TIMEFRAMES = {
    "bias": "15m",
    "setup": "5m",
    "entry": "1m"
}

# Much longer sleep → prevent ban (start conservative!)
SLEEP_BETWEEN_PAIRS = 12          # seconds — 5 pairs/min → \~safe

# ================= EXCHANGE =================
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})

# ================= TELEGRAM =================
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, data=payload, timeout=12)
        if r.status_code != 200:
            print(f"Telegram failed: {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ================= MARKETS =================
def load_usdt_pairs(max_pairs=60):   # ← limit to avoid death by API calls
    markets = exchange.load_markets()
    pairs = []
    for symbol in markets:
        if (
            symbol.endswith("/USDT")
            and markets[symbol].get("active")
            and "BUSD" not in symbol
            and "USDC" not in symbol
        ):
            pairs.append(symbol)
    # Optional: sort by volume (requires extra call — or hardcode top ones)
    return pairs[:max_pairs]   # start small — increase later if stable

SYMBOLS = load_usdt_pairs()

# ================= DATA =================
def fetch_df(symbol, timeframe, limit=300):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v"])
        df["t"] = pd.to_datetime(df["t"], unit="ms")
        return df
    except Exception as e:
        print(f"fetch failed {symbol} {timeframe}: {e}")
        return None

# ================= INDICATORS =================
def liquidity_sweep(df):
    if df is None or len(df) < 5:
        return None
    recent_high = df['h'].iloc[-6:-1].max()   # lookback improved
    recent_low  = df['l'].iloc[-6:-1].min()
    last = df.iloc[-1]
    if last['h'] > recent_high and last['c'] < recent_high * 0.999:
        return "SELL"
    if last['l'] < recent_low and last['c'] > recent_low * 1.001:
        return "BUY"
    return None

def fibonacci_zone(high, low, price):
    if high <= low:
        return False
    for f in [0.618, 0.705, 0.786]:
        level = high - (high - low) * f
        if abs(price - level) / price < 0.005:   # widened to 0.5%
            return True
    return False

def volume_ok(df):
    if df is None or len(df) < 25:
        return False
    avg = df["v"].rolling(20).mean().iloc[-1]
    return df["v"].iloc[-1] > avg * 1.3   # stricter

# ================= CORE =================
def analyze(symbol):
    try:
        df15 = fetch_df(symbol, TIMEFRAMES["bias"])
        if df15 is None: return

        df5 = fetch_df(symbol, TIMEFRAMES["setup"])
        if df5 is None: return

        df1 = fetch_df(symbol, TIMEFRAMES["entry"], limit=150)
        if df1 is None: return

        sweep = liquidity_sweep(df5)
        if sweep is None:
            return

        bias = "BULLISH" if df15["c"].iloc[-1] > df15["o"].iloc[-1] else "BEARISH"
        price = float(df1["c"].iloc[-1])

        if not volume_ok(df1):
            return

        high = df5["h"].max()
        low = df5["l"].min()

        if not fibonacci_zone(high, low, price):
            return

        if sweep == "SELL":
            sl = price * 1.004
            tp1 = price - (sl - price) * 1.5
            tp2 = price - (sl - price) * 2.5
        else:
            sl = price * 0.996
            tp1 = price + (price - sl) * 1.5
            tp2 = price + (price - sl) * 2.5

        msg = f"""**SCALPING ALERT**

**PAIR:** {symbol}
**BIAS 15M:** {bias}
**DIRECTION:** {sweep}

**ENTRY:** {price:.6f}
**SL:**     {sl:.6f}
**TP1:**    {tp1:.6f}
**TP2:**    {tp2:.6f}

**CAPITAL:** {CAPITAL} USDT
**RISK:**    {RISK_PERCENT}%"""

        send_telegram(msg)
        print(f"Alert sent → {symbol} {sweep}")

    except Exception as e:
        err = traceback.format_exc()
        print(f"ERROR {symbol}: {e}\n{err}")
        send_telegram(f"ERROR on {symbol}: {str(e)[:200]}")

# ================= START =================
if __name__ == "__main__":
    print(f"BOT STARTED - Binance Spot - {len(SYMBOLS)} pairs")
    send_telegram(f"Bot started on Railway — scanning {len(SYMBOLS)} pairs")

    while True:
        try:
            for s in SYMBOLS:
                analyze(s)
                time.sleep(SLEEP_BETWEEN_PAIRS)
            
            # Optional: reload symbols every 4 hours (new listings)
            time.sleep(60 * 60 * 4)
            global SYMBOLS
            SYMBOLS = load_usdt_pairs()
            print(f"Reloaded symbols — now {len(SYMBOLS)} pairs")

        except KeyboardInterrupt:
            print("Stopped by user")
            break
        except Exception as e:
            print(f"Main loop crash: {e}")
            time.sleep(300)   # wait 5 min before retry
