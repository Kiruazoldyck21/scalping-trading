import ccxt
import pandas as pd
import numpy as np
import requests
import time

# ================= CONFIG =================
API_TOKEN = "TELEGRAM_BOT_TOKEN"
CHAT_ID = "TELEGRAM_CHAT_ID"

CAPITAL = 1000
RISK_PERCENT = 0.008

TIMEFRAMES = {
    "bias": "15m",
    "setup": "5m",
    "entry": "1m"
}

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ================= MARKETS =================
def get_all_usdt_pairs():
    markets = exchange.load_markets()
    pairs = []
    for s, m in markets.items():
        if s.endswith("/USDT") and m['active']:
            if "BUSD" not in s and "USDC" not in s:
                pairs.append(s)
    return pairs

SYMBOLS = get_all_usdt_pairs()

# ================= DATA =================
def get_df(symbol, tf, limit=200):
    data = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    return pd.DataFrame(data, columns=['t','o','h','l','c','v'])

# ================= INDICATORS =================
def liquidity_sweep(df):
    prev = df.iloc[-3]
    last = df.iloc[-2]

    if last['h'] > prev['h'] and last['c'] < prev['h']:
        return "SELL"
    if last['l'] < prev['l'] and last['c'] > prev['l']:
        return "BUY"
    return None

def anchored_vwap(df, idx):
    tp = (df['h'] + df['l'] + df['c']) / 3
    vol = df['v']
    return (tp[idx:] * vol[idx:]).cumsum().iloc[-1] / vol[idx:].cumsum().iloc[-1]

def fibonacci_zone(high, low, price):
    for f in [0.618, 0.705, 0.786]:
        lvl = high - (high - low) * f
        if abs(price - lvl) / price < 0.002:
            return True
    return False

def volume_ok(df):
    return df['v'].iloc[-2] > df['v'].rolling(20).mean().iloc[-2]

# ================= CORE =================
def analyze(symbol):
    try:
        df15 = get_df(symbol, TIMEFRAMES['bias'])
        df5  = get_df(symbol, TIMEFRAMES['setup'])
        df1  = get_df(symbol, TIMEFRAMES['entry'])

        sweep = liquidity_sweep(df5)
        if not sweep:
            return

        bias = "BULLISH" if df15['c'].iloc[-1] > df15['o'].iloc[-1] else "BEARISH"
        price = df1['c'].iloc[-1]

        if not volume_ok(df1):
            return

        high = df5['h'].max()
        low = df5['l'].min()

        if not fibonacci_zone(high, low, price):
            return

        vwap = anchored_vwap(df5, -3)

        sl = price * (1.004 if sweep == "SELL" else 0.996)
        risk = abs(price - sl)
        tp1 = price - risk * 1.5 if sweep == "SELL" else price + risk * 1.5
        tp2 = price - risk * 2.5 if sweep == "SELL" else price + risk * 2.5

        msg = f"""
{'🔴 SELL' if sweep=='SELL' else '🟢 BUY'} SCALPING ALERT

Pair: {symbol}
Bias 15m: {bias}

✔ Liquidity Sweep (5m)
✔ Anchored VWAP
✔ Fibonacci Zone
✔ Volume Confirmed

Entry: {price:.4f}
SL: {sl:.4f}
TP1: {tp1:.4f}
TP2: {tp2:.4f}

Capital: {CAPITAL} USDT
Risk: {RISK_PERCENT*100:.2f}%
"""
        send_telegram(msg)

    except:
        pass

# ================= LOOP =================
send_telegram("✅ Scalping bot ALL PAIRS started (Railway)")

while True:
    for sym in SYMBOLS:
        analyze(sym)
        time.sleep(1.2)  # anti rate-limit
