import asyncio
import logging
import os
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
)

# ===== CONFIG =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("La variable d'environnement TELEGRAM_TOKEN est manquante ou vide !")

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== GLOBAL ERROR HANDLER =====
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    if update and hasattr(update, 'effective_chat') and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ Erreur dans le bot : {context.error}\nVérifiez les logs Railway."
            )
        except Exception:
            pass

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

    chat_id = context.job.data.get('chat_id')
    tf = CURRENT_TF
    htf = "1h" if tf == "5m" else "4h"

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

            await asyncio.sleep(1.5)

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
        "Choisissez le timeframe pour commencer la surveillance.\n"
        "Les signaux seront envoyés ici.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    global WATCHING, CURRENT_TF

    # Safety check – job_queue may be None during startup
    if context.job_queue is None:
        await query.edit_message_text(
            "⚠️ Le bot n'est pas encore complètement prêt.\n"
            "Attendez 10–20 secondes et réessayez le bouton."
        )
        logger.warning("job_queue était None – bouton pressé trop tôt")
        return

    if query.data == "start_5m":
        WATCHING = True
        CURRENT_TF = "5m"
        await query.edit_message_text("Surveillance **5m** démarrée.\nLes signaux apparaîtront ici.")
        context.job_queue.run_repeating(
            scan_pairs,
            interval=300,
            first=5,
            data={'chat_id': query.message.chat_id},
            name="signal_scanner"
        )

    elif query.data == "start_15m":
        WATCHING = True
        CURRENT_TF = "15m"
        await query.edit_message_text("Surveillance **15m** démarrée.\nLes signaux apparaîtront ici.")
        context.job_queue.run_repeating(
            scan_pairs,
            interval=300,
            first=5,
            data={'chat_id': query.message.chat_id},
            name="signal_scanner"
        )

    elif query.data == "stop":
        WATCHING = False
        # Clean up jobs
        for job in context.job_queue.get_jobs_by_name("signal_scanner"):
            job.schedule_removal()
        await query.edit_message_text("🛑 Surveillance arrêtée.")

    elif query.data == "list":
        pairs_text = "\n".join(f"• {p}" for p in PAIRS)
        await query.edit_message_text(f"**Liste des paires** ({len(PAIRS)}):\n{pairs_text}")

    elif query.data == "help":
        await query.edit_message_text(
            "Aide :\n"
            "• Start 5m / 15m → lance les signaux\n"
            "• Stop Watching → arrête les alertes\n"
            "• Signaux : EMA9/21 + RSI + Volume + filtre TF supérieur\n"
            "• Données : Bybit public\n"
            "/start pour ouvrir le menu."
        )

# ===== MAIN =====
async def main_async():
    # Give Railway container time to stabilize network
    await asyncio.sleep(8)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # status command
    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = "Active" if WATCHING else "Stopped"
        tf = CURRENT_TF if WATCHING else "-"
        await update.message.reply_text(f"Status: **{state}**\nTimeframe: **{tf}**")

    app.add_handler(CommandHandler("status", status))

    await app.initialize()
    await app.start()

    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        bootstrap_retries=10,
        drop_pending_updates=True,
        poll_interval=0.5,
        timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30,
    )

    logger.info("Bot polling démarré avec succès – envoyez /start pour tester")

    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main_async())
