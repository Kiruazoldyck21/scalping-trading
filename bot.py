import time
import pandas as pd
import requests
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

# ===== CONFIG =====
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
TIMEFRAME = "5m"
HTF_TIMEFRAME = "1h"           # Now using 1-hour for stronger trend filter

TP_PERCENT = 0.005
SL_PERCENT = 0.003

# Volume Filter Config
VOLUME_MA_PERIOD = 20
VOLUME_MULTIPLIER = 1.3

client = Client()  # Public data only — no API keys needed

# ===== TELEGRAM =====
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=data)

# ===== DATA =====
def get_data(symbol, interval, limit=200):  # Increased limit for safety on 1h
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'time','open','high','low','close','volume',
        '_','_','_','_','_','_'
    ])
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

# ===== SIGNAL CHECK =====
def check_signal(df_5m, df_1h):
    if len(df_5m) < 50 or len(df_1h) < 50:
        return None, None

    # 5m indicators (entry timeframe)
    rsi_5m    = RSIIndicator(df_5m['close'], 14).rsi()
    ema9_5m   = EMAIndicator(df_5m['close'], 9).ema_indicator()
    ema21_5m  = EMAIndicator(df_5m['close'], 21).ema_indicator()

    # 1h higher TF trend filter (EMA9 vs EMA21 on 1h)
    ema9_1h  = EMAIndicator(df_1h['close'], 9).ema_indicator().iloc[-1]
    ema21_1h = EMAIndicator(df_1h['close'], 21).ema_indicator().iloc[-1]
    htf_bullish = ema9_1h > ema21_1h
    htf_bearish = ema9_1h < ema21_1h

    price = df_5m['close'].iloc[-1]
    current_volume = df_5m['volume'].iloc[-1]

    # Volume filter (on 5m)
    vol_ma = df_5m['volume'].rolling(window=VOLUME_MA_PERIOD).mean().iloc[-1]
    high_volume = current_volume > (vol_ma * VOLUME_MULTIPLIER) if pd.notna(vol_ma) else False

    signal = None

    # BUY: 5m pullback + volume + 1h bullish trend
    if (ema9_5m.iloc[-1] > ema21_5m.iloc[-1] and
        30 < rsi_5m.iloc[-1] < 45 and
        high_volume and
        htf_bullish):
        signal = "BUY"

    # SELL: 5m pullback + volume + 1h bearish trend
    elif (ema9_5m.iloc[-1] < ema21_5m.iloc[-1] and
          55 < rsi_5m.iloc[-1] < 70 and
          high_volume and
          htf_bearish):
        signal = "SELL"

    return signal, price

# ===== MAIN LOOP =====
send_telegram(f"🚀 Spot Scalping Signal Bot Started\n"
              f"5m + Volume Filter + {HTF_TIMEFRAME} EMA9/21 Trend Filter")

last_signals = {}  # pair → (signal, price) to prevent duplicates

while True:
    try:
        for pair in PAIRS:
            df_5m = get_data(pair, TIMEFRAME, limit=150)
            df_1h = get_data(pair, HTF_TIMEFRAME, limit=100)  # 1h needs fewer candles

            signal, price = check_signal(df_5m, df_1h)

            if signal:
                key = pair
                prev = last_signals.get(key)
                if prev and prev[0] == signal and abs(price - prev[1]) / prev[1] < 0.001:
                    continue  # Skip near-identical duplicate

                last_signals[key] = (signal, price)

                if signal == "BUY":
                    tp = price * (1 + TP_PERCENT)
                    sl = price * (1 - SL_PERCENT)
                else:
                    tp = price * (1 - TP_PERCENT)
                    sl = price * (1 + SL_PERCENT)

                msg = (
                    f"📊 {pair} ({TIMEFRAME})\n"
                    f"Signal: {signal} (Vol + {HTF_TIMEFRAME} EMA Filter)\n"
                    f"Entry: {price:.4f}\n"
                    f"TP: {tp:.4f}\n"
                    f"SL: {sl:.4f}"
                )
                send_telegram(msg)

        time.sleep(60 * 5)  # Check roughly every 5 minutes

    except Exception as e:
        send_telegram(f"⚠️ Error: {str(e)}")
        time.sleep(60)
