import time
import pandas as pd
import requests
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

# ===== CONFIG =====
API_KEY = ""
API_SECRET = ""
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
TIMEFRAMES = ["1m", "5m"]

TP_PERCENT = 0.005
SL_PERCENT = 0.003

client = Client(API_KEY, API_SECRET)

# ===== TELEGRAM =====
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=data)

# ===== DATA =====
def get_data(symbol, interval):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=100)
    df = pd.DataFrame(klines, columns=[
        'time','open','high','low','close','volume',
        '_','_','_','_','_','_'
    ])
    df['close'] = df['close'].astype(float)
    return df

# ===== SIGNAL =====
def check_signal(df):
    rsi = RSIIndicator(df['close'], 14).rsi()
    ema9 = EMAIndicator(df['close'], 9).ema_indicator()
    ema21 = EMAIndicator(df['close'], 21).ema_indicator()

    price = df['close'].iloc[-1]

    if ema9.iloc[-1] > ema21.iloc[-1] and 30 < rsi.iloc[-1] < 45:
        return "BUY", price
    if ema9.iloc[-1] < ema21.iloc[-1] and 55 < rsi.iloc[-1] < 70:
        return "SELL", price
    return None, price

# ===== MAIN LOOP =====
send_telegram("🚀 Scalping Bot Started")

while True:
    try:
        for pair in PAIRS:
            for tf in TIMEFRAMES:
                df = get_data(pair, tf)
                signal, price = check_signal(df)

                if signal:
                    if signal == "BUY":
                        tp = price * (1 + TP_PERCENT)
                        sl = price * (1 - SL_PERCENT)
                    else:
                        tp = price * (1 - TP_PERCENT)
                        sl = price * (1 + SL_PERCENT)

                    msg = (
                        f"📊 {pair} ({tf})
"
                        f"Signal: {signal}
"
                        f"Entry: {price:.4f}
"
                        f"TP: {tp:.4f}
"
                        f"SL: {sl:.4f}"
                    )
                    send_telegram(msg)

        time.sleep(60)

    except Exception as e:
        send_telegram(f"⚠️ Error: {e}")
        time.sleep(60)
