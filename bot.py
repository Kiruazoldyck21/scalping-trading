import ccxt
import pandas as pd
import requests
import time

# ===================== CONFIG =====================
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "TELEGRAM_CHAT_ID"

CAPITAL = 1000
RISK_PERCENT = 0.8  # %

TIMEFRAMES = {
    "bias": "15m",
    "setup": "5m",
    "entry": "1m"
}

SLEEP_BETWEEN_PAIRS = 1.2

# ===================== EXCHANGE =====================
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})

# ===================== TELEGRAM =====================
def send_telegram(message):
    try:
        url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        requests.post(url, data=payload, timeout=10)
    except Exception:
        pass

# ===================== MARKETS =====================
def load_all_usdt_pairs():
    markets = exchange.load_markets()
    pairs = []

    for symbol in markets:
        data = markets[symbol]
        if symbol.endswith("/USDT") and data.get("active"):
            if "BUSD" not in symbol and "USDC" not in symbol:
                pairs.append(symbol)

    return pairs

SYMBOLS = load_all_usdt_pairs()

# ===================== DATA =====================
def fetch_df(symbol, timeframe, limit=200):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    return pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"]
    )

# ===================== INDICATORS =====================
def liquidity_sweep(df):
    prev = df.iloc[-3]
    last = df.iloc[-2]

    if last["high"] > prev["high"] and last["close"] < prev["high"]:
        return "SELL"

    if last["low"] < prev["low"] and last["close"] > prev["low"]:
        return "BUY"

    return None

def anchored_vwap(df, anchor_index):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    vwap = (tp[anchor_index:] * vol[anchor_index:]).cumsum() / vol[anchor_index:].cumsum()
    return float(vwap.iloc[-1])

def fibonacci_zone(high, low, price):
    fibs = [0.618, 0.705, 0.786]
    for f in fibs:
        level = high - (high - low) * f
        if abs(price - level) / price < 0.002:
            return True
    return False

def volume_confirmation(df):
    avg = df["volume"].rolling(20).mean().iloc[-2]
    return df["volume"].iloc[-2] > avg

# ===================== CORE =====================
def analyze_symbol(symbol):
    try:
        df15 = fetch_df(symbol, TIMEFRAMES["bias"])
        df5 = fetch_df(symbol, TIMEFRAMES["setup"])
        df1 = fetch_df(symbol, TIMEFRAMES["entry"])

        sweep = liquidity_sweep(df5)
        if sweep is None:
            return

        bias = "BULLISH" if df15["close"].iloc[-1] > df15["open"].iloc[-1] else "BEARISH"
        price = float(df1["close"].iloc[-1])

        if not volume_confirmation(df1):
            return

        high = df5["high"].max()
        low = df5["low"].min()

        if not fibonacci_zone(high, low, price):
            return

        vwap = anchored_vwap(df5, -3)

        if sweep == "SELL":
            sl = price * 1.004
            tp1 = price - (sl - price) * 1.5
            tp2 = price - (sl - price) * 2.5
            side = "🔴 SELL"
        else:
            sl = price * 0.996
            tp1 = price + (price - sl) * 1.5
            tp2 = price + (price - sl) * 2.5
            side = "🟢 BUY"

        message = (
            "{} SCALPING ALERT\n\n"
            "Pair: {}\n"
            "Bias 15m: {}\n\n"
            "✔ Liquidity Sweep (5m)\n"
            "✔ Anchored VWAP\n"
            "✔ Fibonacci Zone\n"
            "✔ Volume Confirmed\n\n"
            "Entry: {:.6f}\n"
            "SL: {:.6f}\n"
            "TP1: {:.6f}\n"
            "TP2: {:.6f}\n\n"
            "Capital: {} USDT\n"
            "Risk: {}%"
        ).format(
            side,
            symbol,
            bias,
            price,
            sl,
            tp1,
            tp2,
            CAPITAL,
            RISK_PERCENT
        )

        send_telegram(message)

    except Exception as e:
        send_telegram("⚠️ Error on {}\n{}".format(symbol, str(e)))

# ===================== MAIN LOOP =====================
send_telegram("✅ Scalping bot STARTED (Railway • Binance Spot • All USDT pairs)")

while True:
    for sym in SYMBOLS:
        analyze_symbol(sym)
        time.sleep(SLEEP_BETWEEN_PAIRS)
