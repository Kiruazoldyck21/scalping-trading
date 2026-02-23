import ccxt
import pandas as pd
import requests
import time

# ================= CONFIG =================
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "TELEGRAM_CHAT_ID"

CAPITAL = 1000
RISK_PERCENT = 0.8

TIMEFRAMES = {
    "bias": "15m",
    "setup": "5m",
    "entry": "1m"
}

SLEEP_BETWEEN_PAIRS = 1.5

# ================= EXCHANGE =================
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})

# ================= TELEGRAM =================
def send_telegram(message):
    url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception:
        pass

# ================= MARKETS =================
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

# ================= DATA =================
def fetch_df(symbol, timeframe, limit=200):
    data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    return pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v"])

# ================= INDICATORS =================
def liquidity_sweep(df):
    prev = df.iloc[-3]
    last = df.iloc[-2]

    if last["h"] > prev["h"] and last["c"] < prev["h"]:
        return "SELL"
    if last["l"] < prev["l"] and last["c"] > prev["l"]:
        return "BUY"
    return None

def fibonacci_zone(high, low, price):
    for f in [0.618, 0.705, 0.786]:
        level = high - (high - low) * f
        if abs(price - level) / price < 0.002:
            return True
    return False

def volume_ok(df):
    avg = df["v"].rolling(20).mean().iloc[-2]
    return df["v"].iloc[-2] > avg

# ================= CORE =================
def analyze(symbol):
    try:
        df15 = fetch_df(symbol, TIMEFRAMES["bias"])
        df5 = fetch_df(symbol, TIMEFRAMES["setup"])
        df1 = fetch_df(symbol, TIMEFRAMES["entry"])

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

        msg = (
            "SCALPING ALERT\n\n"
            "PAIR: {}\n"
            "BIAS 15M: {}\n"
            "DIRECTION: {}\n\n"
            "ENTRY: {:.6f}\n"
            "SL: {:.6f}\n"
            "TP1: {:.6f}\n"
            "TP2: {:.6f}\n\n"
            "CAPITAL: {} USDT\n"
            "RISK: {}%"
        ).format(
            symbol,
            bias,
            sweep,
            price,
            sl,
            tp1,
            tp2,
            CAPITAL,
            RISK_PERCENT
        )

        send_telegram(msg)

    except Exception as e:
        send_telegram("ERROR on {} : {}".format(symbol, str(e)))

# ================= LOOP =================
send_telegram("BOT STARTED - BINANCE SPOT - ALL USDT PAIRS")

while True:
    for s in SYMBOLS:
        analyze(s)
        time.sleep(SLEEP_BETWEEN_PAIRS)
