import asyncio
import logging
import pandas as pd
import ccxt
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
)

# ===== CONFIG =====
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # Replace!

PAIRS = [
    "BTC/USDT", "ETH/USDT", "THE/USDT", "PHA/USDT", "SOMI/USDT",
    "ARPA/USDT", "PYTH/USDT", "TIA/USDT", "ALPINE/USDT", "REI/USDT",
    "RIF/USDT", "SUI/USDT", "PORTAL/USDT", "PARTI/USDT", "XLM/USDT"
]

VOLUME_MA_PERIOD = 20
VOLUME_MULTIPLIER = 1.3

TP_PERCENT = 0.005
SL_PERCENT = 0.003

# Global state (simple – for production use context.user_data or DB)
WATCHING = False
CURRENT_TF = "5m"          # default

exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'},
})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== DATA FETCH =====
async def get_data(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    except Exception as e:
        logger.error(f"Fetch error {symbol} {interval}: {e}")
        return pd.DataFrame()

# ===== SIGNAL CHECK =====
async def check_signal(df_main: pd.DataFrame, df_htf: pd.DataFrame) -> tuple:
    if len(df_main) < 50 or len(df_htf) < 50:
        return None, None

    rsi = RSIIndicator(df_main['close'], 14).rsi()
    ema9 = EMAIndicator(df_main['close'], 9).ema_indicator()
    ema21 = EMAIndicator(df_main['close'], 21).ema_indicator()

    ema9_htf = EMAIndicator(df_htf['close'], 9).ema_indicator().iloc[-1]
    ema21_htf = EMAIndicator(df_htf['close'], 21).ema_indicator().iloc[-1]
    htf_bullish = ema9_htf > ema21_htf
    htf_bearish = ema9_htf < ema21_htf

    price = df_main['close'].iloc[-1]
    vol = df_main['volume'].iloc[-1]
    vol_ma = df_main['volume'].rolling(window=VOLUME_MA_PERIOD).mean().iloc[-1]
    high_vol = vol > (vol_ma * VOLUME_MULTIPLIER) if pd.notna(vol_ma) else False

    signal = None

    if (ema9.iloc[-1] > ema21.iloc[-1] and
        30 < rsi.iloc[-1] < 45 and
        high_vol and htf_bullish):
        signal = "BUY"

    elif (ema9.iloc[-1] < ema21.iloc[-1] and
          55 < rsi.iloc[-1] < 70 and
          high_vol and htf_bearish):
        signal = "SELL"

    return signal, price

# ===== BACKGROUND SIGNAL CHECKER =====
async def scan_pairs(context: ContextTypes.DEFAULT_TYPE):
    global WATCHING, CURRENT_TF

    if not WATCHING:
        return

    chat_id = context.job.data.get('chat_id')  # stored when starting
    tf = CURRENT_TF
    htf = "1h" if tf == "5m" else "4h"  # reasonable higher TF

    for pair in PAIRS:
        try:
            df_main = await get_data(pair, tf, 200)
            df_htf = await get_data(pair, htf, 100)

            if df_main.empty or df_htf.empty:
                continue

            signal, price = await check_signal(df_main, df_htf)

            if signal:
                tp = price * (1 + TP_PERCENT) if signal == "BUY" else price * (1 - TP_PERCENT)
                sl = price * (1 - SL_PERCENT) if signal == "BUY" else price * (1 + SL_PERCENT)

                msg = (
                    f"📊 **{pair}** ({tf})\n"
                    f"Signal: **{signal}**\n"
                    f"Price: `{price:.4f}`\n"
                    f"TP: `{tp:.4f}`\n"
                    f"SL: `{sl:.4f}`\n"
                    f"Volume + higher TF filter"
                )
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

            await asyncio.sleep(1.5)  # gentle rate limit

        except Exception as e:
            logger.error(e)

# ===== MENU HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Start 5m Watch", callback_data="start_5m")],
        [InlineKeyboardButton("Start 15m Watch", callback_data="start_15m")],
        [InlineKeyboardButton("Stop Watching", callback_data="stop")],
        [InlineKeyboardButton("Watch List", callback_data="list")],
        [InlineKeyboardButton("Status / Help", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚀 Scalping Signal Bot\n\n"
        "Choose timeframe to start watching signals on the pair list.\n"
        "Signals sent here when detected.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    global WATCHING, CURRENT_TF

    if query.data == "start_5m":
        WATCHING = True
        CURRENT_TF = "5m"
        await query.edit_message_text("Started watching **5m** timeframe.\nSignals will appear here.")
        context.job_queue.run_repeating(
            scan_pairs,
            interval=300,           # 5 minutes
            first=10,
            data={'chat_id': query.message.chat_id},
            name="signal_scanner"
        )

    elif query.data == "start_15m":
        WATCHING = True
        CURRENT_TF = "15m"
        await query.edit_message_text("Started watching **15m** timeframe.\nSignals will appear here.")
        context.job_queue.run_repeating(
            scan_pairs,
            interval=300,
            first=10,
            data={'chat_id': query.message.chat_id},
            name="signal_scanner"
        )

    elif query.data == "stop":
        WATCHING = False
        # Optional: remove job if you want strict cleanup
        # current_jobs = context.job_queue.get_jobs_by_name("signal_scanner")
        # for job in current_jobs: job.schedule_removal()
        await query.edit_message_text("🛑 Stopped watching. No more signals until restarted.")

    elif query.data == "list":
        pairs_text = "\n".join(f"• {p}" for p in PAIRS)
        await query.edit_message_text(f"**Watch List** ({len(PAIRS)} pairs):\n{pairs_text}")

    elif query.data == "help":
        await query.edit_message_text(
            "Help & Status:\n"
            "• Start 5m / 15m → begin receiving signals\n"
            "• Stop Watching → pause alerts\n"
            "• Signals: EMA9/21 + RSI pullback + Volume spike + higher TF filter\n"
            "• Data: Bybit public API\n"
            "Use /start to open menu anytime."
        )

# ===== MAIN =====
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(CallbackQueryHandler(button_handler))

    # Optional extra commands
    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = "Active" if WATCHING else "Stopped"
        tf = CURRENT_TF if WATCHING else "-"
        await update.message.reply_text(f"Status: **{state}**\nTimeframe: **{tf}**")

    app.add_handler(CommandHandler("status", status))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
