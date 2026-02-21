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
    raise ValueError("Token manquant !")

PAIRS = [
    "BTC/USDT", "ETH/USDT", "THE/USDT", "PHA/USDT", "SOMI/USDT",
    "ARPA/USDT", "PYTH/USDT", "TIA/USDT", "ALPINE/USDT", "REI/USDT",
    "RIF/USDT", "SUI/USDT", "PORTAL/USDT", "PARTI/USDT", "XLM/USDT"
]

VOLUME_MA_PERIOD = 20
VOLUME_MULTIPLIER = 1.3
TP_PERCENT = 0.005
SL_PERCENT = 0.003

exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'},
})

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== VARIABLES GLOBALES (solution simple) =====
watching = False
current_tf = "5m"
scan_task = None

# ===== DATA FETCH =====
async def get_data(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
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

    if (ema9.iloc[-1] > ema21.iloc[-1] and 30 < rsi.iloc[-1] < 45 and high_vol and htf_bullish):
        return "BUY", price
    elif (ema9.iloc[-1] < ema21.iloc[-1] and 55 < rsi.iloc[-1] < 70 and high_vol and htf_bearish):
        return "SELL", price
    return None, None

# ===== SCAN FUNCTION =====
async def scan_pairs(chat_id, tf):
    """Version simplifiée sans context"""
    global watching
    
    htf = "1h" if tf == "5m" else "4h"
    logger.info(f"Scan en cours - TF: {tf}")

    for pair in PAIRS:
        if not watching:  # Vérifier si on doit continuer
            return
            
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
                    f"Prix: `{price:.4f}`\n"
                    f"TP: `{tp:.4f}`\n"
                    f"SL: `{sl:.4f}`"
                )
                # Ici il faudra passer le bot en paramètre
                logger.info(f"SIGNAL: {pair} - {signal}")

            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Erreur {pair}: {e}")

# ===== BOUCLE PRINCIPALE =====
async def scanning_loop(app):
    """Boucle qui tourne en arrière-plan"""
    global watching, current_tf
    
    logger.info("🔄 Boucle de scan démarrée")
    
    while True:
        try:
            if watching:
                logger.info(f"Scan actif - TF: {current_tf}")
                
                htf = "1h" if current_tf == "5m" else "4h"
                
                for pair in PAIRS:
                    if not watching:
                        break
                        
                    try:
                        df_main = await get_data(pair, current_tf, 200)
                        df_htf = await get_data(pair, htf, 100)

                        if df_main.empty or df_htf.empty:
                            continue

                        signal, price = await check_signal(df_main, df_htf)

                        if signal:
                            tp = price * (1 + TP_PERCENT) if signal == "BUY" else price * (1 - TP_PERCENT)
                            sl = price * (1 - SL_PERCENT) if signal == "BUY" else price * (1 + SL_PERCENT)

                            msg = (
                                f"📊 **{pair}** ({current_tf})\n"
                                f"Signal: **{signal}**\n"
                                f"Prix: `{price:.4f}`\n"
                                f"TP: `{tp:.4f}`\n"
                                f"SL: `{sl:.4f}`"
                            )
                            
                            # Envoyer le message
                            if app.bot_data.get('chat_id'):
                                await app.bot.send_message(
                                    chat_id=app.bot_data['chat_id'],
                                    text=msg,
                                    parse_mode="Markdown"
                                )

                        await asyncio.sleep(1)

                    except Exception as e:
                        logger.error(f"Erreur {pair}: {e}")
            
            # Attendre 60 secondes avant le prochain scan complet
            for _ in range(60):
                if not watching:
                    break
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Erreur dans boucle principale: {e}")
            await asyncio.sleep(10)

# ===== HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚀 Start 5m", callback_data="start_5m")],
        [InlineKeyboardButton("⚡ Start 15m", callback_data="start_15m")],
        [InlineKeyboardButton("🛑 Stop", callback_data="stop")],
        [InlineKeyboardButton("📋 Liste", callback_data="list")],
        [InlineKeyboardButton("❓ Aide", callback_data="help")],
    ]
    await update.message.reply_text(
        "🤖 **Bot de Signaux**\nChoisissez une option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global watching, current_tf
    
    query = update.callback_query
    await query.answer()

    # Sauvegarder le chat_id
    context.bot_data['chat_id'] = query.message.chat_id

    if query.data == "start_5m":
        watching = True
        current_tf = "5m"
        await query.edit_message_text("✅ Surveillance **5m** activée!\nLes signaux apparaîtront ici.")
        logger.info("Surveillance 5m activée")

    elif query.data == "start_15m":
        watching = True
        current_tf = "15m"
        await query.edit_message_text("✅ Surveillance **15m** activée!\nLes signaux apparaîtront ici.")
        logger.info("Surveillance 15m activée")

    elif query.data == "stop":
        watching = False
        await query.edit_message_text("🛑 Surveillance arrêtée")
        logger.info("Surveillance arrêtée")

    elif query.data == "list":
        pairs_text = "\n".join(f"• {p}" for p in PAIRS[:10])
        await query.edit_message_text(f"**Paires** (10/{len(PAIRS)}):\n{pairs_text}")

    elif query.data == "help":
        await query.edit_message_text(
            "**Aide**\n"
            "• Start: Active la surveillance\n"
            "• Stop: Désactive\n"
            "• Signaux basés sur EMA/RSI/Volume\n"
            "• Scan toutes les 60 secondes"
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global watching, current_tf
    await update.message.reply_text(
        f"📊 **Statut**\n"
        f"Actif: {'✅ Oui' if watching else '❌ Non'}\n"
        f"Timeframe: {current_tf}\n"
        f"Paires: {len(PAIRS)}"
    )

# ===== MAIN =====
async def main():
    global scan_task
    
    logger.info("🚀 Démarrage du bot...")

    # Construction simple
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("status", status))

    # Init bot_data
    app.bot_data['chat_id'] = None

    # Démarrage
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.info("✅ Bot démarré!")

    # Démarrer la boucle de scan en arrière-plan
    asyncio.create_task(scanning_loop(app))

    # Garder en vie
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Arrêt...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
