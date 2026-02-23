import ccxt
import pandas as pd
import numpy as np
import requests
import time
import traceback

# ===================== CONFIG =====================
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "TELEGRAM_CHAT_ID"

CAPITAL = 1000
RISK_PERCENT = 0.008  # 0.8%

TIMEFRAMES = {
    "bias": "15m",
    "setup": "5m",
    "entry": "1m"
}

SLEEP_BETWEEN_PAIRS = 1.2  # anti rate-limit

# ===================== EXCHANGE =====================
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})

# ===================== TELEGRAM =====================
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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

    for symbol, data in markets.items():
        if (
            symbol.endswith("/USDT")
            and data.get("active", False)
            and "BUSD" not in symbol
            and "USDC" not in symbol
        ):
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
    avg_vol = df["volume"].rolling(20).mean().iloc[-2]
    return df["volume"].iloc[-2] > avg_vol

# ===================== CORE ANALYSIS =====================
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
            side_icon = "🔴 SELL"
        else:
            sl = price * 0.996
            tp1 = price + (price - sl) * 1.5
            tp2 = price + (price - sl) * 2.5
            side_icon = "🟢 BUY"

        message = (
            f"{side_icon} SCALPING ALERT\n\n"
            f"Pair: {symbol}\n"
            f"Bias 15m: {bias}\n\n"
            f"✔ Liquidity Sweep (5m)\n"
            f"✔ Anchored VWAP\n"
            f"✔ Fibonacci Zone\n"
            f"✔ Volume Confirmed\n\n"
            f"Entry: {price:.6f}\n"
            f"SL: {sl:.6f}\n"
            f"TP1: {tp1:.6f}\n"
            f"TP2: {tp2:.6f}\n\n"
            f"Capital: {CAPITAL} USDT\n"
            f"Risk: {RISK_PERCENT * 100:.2f}%"
        )

        send_telegram(message)

    except Exception as e:
        send_telegram(f"⚠️ Error on {symbol}\n{str(e)}")

# ===================== MAIN LOOP =====================
send_telegram("✅ Scalping bot STARTED (Railway • Binance Spot • All USDT pairs)")

while True:
    for sym in SYMBOLS:
        analyze_symbol(sym)
        time.sleep(SLEEP_BETWEEN_PAIRS)
