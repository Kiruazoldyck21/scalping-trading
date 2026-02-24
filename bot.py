import ccxt
import pandas as pd
import requests
import time
import os
import logging
import json
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

# ────────────────────────────────────────────────
#               CONFIGURATION
# ────────────────────────────────────────────────

# Railway environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CAPITAL = float(os.getenv("CAPITAL", "1000.0"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.8"))
MAX_PAIRS_TO_SCAN = int(os.getenv("MAX_PAIRS", "50"))
SLEEP_BETWEEN_PAIRS = float(os.getenv("SLEEP_PAIRS", "10.0"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "21600"))  # 6 hours default

# Validate required environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    error_msg = "ERROR: Telegram BOT_TOKEN and CHAT_ID must be set as environment variables"
    print(error_msg)
    raise ValueError(error_msg)

# Timeframes
TIMEFRAMES = {
    "bias": os.getenv("TF_BIAS", "15m"),
    "setup": os.getenv("TF_SETUP", "5m"),
    "entry": os.getenv("TF_ENTRY", "1m")
}

# ────────────────────────────────────────────────
#               LOGGING SETUP
# ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
#               EXCHANGE SETUP
# ────────────────────────────────────────────────

class ExchangeManager:
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'timeout': 30000,
        })
        self.market_cache = {}
        self.cache_timestamp = 0
        self.cache_duration = 3600  # 1 hour
        
    def get_markets(self, force_refresh=False):
        """Get markets with caching"""
        current_time = time.time()
        if force_refresh or (current_time - self.cache_timestamp > self.cache_duration):
            try:
                self.market_cache = self.exchange.load_markets()
                self.cache_timestamp = current_time
                logger.info(f"Markets refreshed: {len(self.market_cache)} pairs")
            except Exception as e:
                logger.error(f"Failed to load markets: {e}")
                if not self.market_cache:
                    raise
        return self.market_cache
    
    def fetch_ohlcv_with_retry(self, symbol: str, timeframe: str, limit: int = 300, max_retries: int = 3) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data with retry logic"""
        for attempt in range(max_retries):
            try:
                data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                return df
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to fetch {symbol} {timeframe} after {max_retries} attempts: {e}")
                    return None
                wait_time = 2 ** attempt  # Exponential backoff
                logger.warning(f"Retry {attempt + 1}/{max_retries} for {symbol} {timeframe} in {wait_time}s")
                time.sleep(wait_time)
        return None

# ────────────────────────────────────────────────
#               TELEGRAM HANDLER
# ────────────────────────────────────────────────

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        
    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send message to Telegram"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected Telegram error: {e}")
            return False
    
    def send_startup_message(self, pair_count: int):
        """Send startup notification"""
        message = f"""🚀 **Bot Started**
• Scanning **{pair_count}** USDT pairs
• Risk: {RISK_PERCENT}% per trade
• Timeframes: {TIMEFRAMES['bias']}/{TIMEFRAMES['setup']}/{TIMEFRAMES['entry']}
• Mode: Spot

_Ready to scan for setups..._"""
        self.send(message)

# ────────────────────────────────────────────────
#               PAIR MANAGER
# ────────────────────────────────────────────────

class PairManager:
    def __init__(self, exchange_manager: ExchangeManager):
        self.exchange_manager = exchange_manager
        self.pairs = []
        
    def get_usdt_pairs(self, max_pairs: int = MAX_PAIRS_TO_SCAN, force_refresh: bool = False) -> List[str]:
        """Get active USDT trading pairs"""
        try:
            markets = self.exchange_manager.get_markets(force_refresh=force_refresh)
            pairs = [
                symbol for symbol in markets
                if symbol.endswith("/USDT")
                and markets[symbol].get("active", False)
                and "BUSD" not in symbol
                and "USDC" not in symbol
                and "UP" not in symbol  # Exclude leveraged tokens
                and "DOWN" not in symbol
            ]
            
            # Sort by volume if possible (optional)
            # pairs.sort(key=lambda x: markets[x].get('info', {}).get('volume', 0), reverse=True)
            
            self.pairs = pairs[:max_pairs]
            logger.info(f"Loaded {len(self.pairs)} USDT pairs")
            return self.pairs
        except Exception as e:
            logger.error(f"Failed to get pairs: {e}")
            return self.pairs or []

# ────────────────────────────────────────────────
#               INDICATORS
# ────────────────────────────────────────────────

class IndicatorCalculator:
    @staticmethod
    def detect_sweep(df: pd.DataFrame) -> Optional[str]:
        """Detect liquidity sweep pattern"""
        if df is None or len(df) < 8:
            return None
        
        try:
            recent_high = df["high"].iloc[-8:-1].max()
            recent_low = df["low"].iloc[-8:-1].min()
            current = df.iloc[-1]
            
            # Break of high with close below (bearish sweep)
            if current["high"] > recent_high and current["close"] < recent_high:
                return "SELL"
            
            # Break of low with close above (bullish sweep)
            if current["low"] < recent_low and current["close"] > recent_low:
                return "BUY"
            
            return None
        except Exception as e:
            logger.error(f"Sweep detection error: {e}")
            return None
    
    @staticmethod
    def in_fib_zone(high: float, low: float, price: float, tolerance: float = 0.006) -> bool:
        """Check if price is in Fibonacci zone"""
        if high <= low or price <= 0:
            return False
        
        try:
            range_size = high - low
            for ratio in [0.618, 0.705, 0.786]:
                level = high - range_size * ratio
                if abs(price - level) / price < tolerance:
                    return True
            return False
        except Exception as e:
            logger.error(f"Fib zone calculation error: {e}")
            return False
    
    @staticmethod
    def volume_spike(df: pd.DataFrame, multiplier: float = 1.3) -> bool:
        """Detect volume spike"""
        if df is None or len(df) < 25:
            return False
        
        try:
            avg_volume = df["volume"].rolling(window=20).mean().iloc[-1]
            current_volume = df["volume"].iloc[-1]
            return current_volume > avg_volume * multiplier
        except Exception as e:
            logger.error(f"Volume spike calculation error: {e}")
            return False
    
    @staticmethod
    def get_bias(df: pd.DataFrame) -> str:
        """Determine market bias from higher timeframe"""
        if df is None or len(df) < 2:
            return "NEUTRAL"
        
        try:
            last_close = df["close"].iloc[-1]
            last_open = df["open"].iloc[-1]
            return "BULLISH" if last_close > last_open else "BEARISH"
        except Exception:
            return "NEUTRAL"

# ────────────────────────────────────────────────
#               SIGNAL GENERATOR
# ────────────────────────────────────────────────

class SignalGenerator:
    def __init__(self, indicators: IndicatorCalculator):
        self.indicators = indicators
    
    def analyze_symbol(self, symbol: str, exchange_manager: ExchangeManager) -> Optional[Dict[str, Any]]:
        """Analyze a single symbol for trading signals"""
        try:
            # Fetch data for all timeframes
            df_bias = exchange_manager.fetch_ohlcv_with_retry(symbol, TIMEFRAMES["bias"])
            df_setup = exchange_manager.fetch_ohlcv_with_retry(symbol, TIMEFRAMES["setup"])
            df_entry = exchange_manager.fetch_ohlcv_with_retry(symbol, TIMEFRAMES["entry"], limit=150)
            
            if any(df is None for df in [df_bias, df_setup, df_entry]):
                return None
            
            # Detect sweep on setup timeframe
            direction = self.indicators.detect_sweep(df_setup)
            if direction is None:
                return None
            
            # Get current price
            current_price = float(df_entry["close"].iloc[-1])
            
            # Check volume spike
            if not self.indicators.volume_spike(df_entry):
                return None
            
            # Check Fibonacci zone
            high_5m = df_setup["high"].max()
            low_5m = df_setup["low"].min()
            
            if not self.indicators.in_fib_zone(high_5m, low_5m, current_price):
                return None
            
            # Calculate bias
            bias = self.indicators.get_bias(df_bias)
            
            # Calculate risk parameters
            signal = self.calculate_risk_parameters(direction, current_price)
            if signal is None:
                return None
            
            signal.update({
                "symbol": symbol,
                "direction": direction,
                "bias": bias,
                "price": current_price
            })
            
            return signal
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    def calculate_risk_parameters(self, direction: str, price: float) -> Optional[Dict[str, float]]:
        """Calculate stop loss and take profit levels"""
        try:
            if direction == "SELL":
                sl = price * 1.0045  # 0.45% stop loss
                risk_dist = sl - price
                tp1 = price - risk_dist * 1.6
                tp2 = price - risk_dist * 3.0
            else:  # BUY
                sl = price * 0.9955  # 0.45% stop loss
                risk_dist = price - sl
                tp1 = price + risk_dist * 1.6
                tp2 = price + risk_dist * 3.0
            
            # Calculate position size
            risk_usdt = CAPITAL * (RISK_PERCENT / 100)
            qty_approx = risk_usdt / risk_dist if risk_dist > 0 else 0
            
            return {
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "risk_usdt": risk_usdt,
                "qty_approx": qty_approx
            }
            
        except Exception as e:
            logger.error(f"Risk calculation error: {e}")
            return None

# ────────────────────────────────────────────────
#               MESSAGE FORMATTER
# ────────────────────────────────────────────────

class MessageFormatter:
    @staticmethod
    def format_signal(signal: Dict[str, Any]) -> str:
        """Format signal for Telegram"""
        emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
        
        message = f"""**{emoji} {signal['direction']} SIGNAL**  
**{signal['symbol']}**

**15m Bias** • {signal['bias']}  
**Direction** • {signal['direction']}

**Entry** • `{signal['price']:.6f}`  
**Stop**  • `{signal['sl']:.6f}`  
**TP1**   • `{signal['tp1']:.6f}`  
**TP2**   • `{signal['tp2']:.6f}`

**Risk** • {signal['risk_usdt']:.2f} USDT ({RISK_PERCENT}%)  
**Size** • {signal['qty_approx']:.4f} {signal['symbol'].split('/')[0]}

_{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_
_Fib zone • Volume spike • Sweep setup_
"""
        return message
    
    @staticmethod
    def format_error(message: str) -> str:
        """Format error message"""
        return f"⚠️ **Error**\n{message}"

# ────────────────────────────────────────────────
#               BOT CONTROLLER
# ────────────────────────────────────────────────

class TradingBot:
    def __init__(self):
        self.exchange_manager = ExchangeManager()
        self.pair_manager = PairManager(self.exchange_manager)
        self.indicators = IndicatorCalculator()
        self.signal_generator = SignalGenerator(self.indicators)
        self.notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.formatter = MessageFormatter()
        self.scan_count = 0
        self.signal_count = 0
        self._start_time = time.time()
        
    def run(self):
        """Main bot loop"""
        try:
            # Initialize pairs
            pairs = self.pair_manager.get_usdt_pairs()
            self.notifier.send_startup_message(len(pairs))
            
            logger.info(f"Bot started with {len(pairs)} pairs")
            
            while True:
                try:
                    for symbol in pairs:
                        logger.debug(f"Scanning {symbol}")
                        
                        # Analyze symbol
                        signal = self.signal_generator.analyze_symbol(symbol, self.exchange_manager)
                        
                        if signal:
                            # Send alert
                            message = self.formatter.format_signal(signal)
                            if self.notifier.send(message):
                                self.signal_count += 1
                                logger.info(f"Signal sent for {symbol}")
                            
                            # Save signal to file (optional)
                            self.save_signal(signal)
                        
                        self.scan_count += 1
                        
                        # Progress log
                        if self.scan_count % 50 == 0:
                            logger.info(f"Scan progress: {self.scan_count} symbols, {self.signal_count} signals")
                        
                        # Sleep between pairs
                        time.sleep(SLEEP_BETWEEN_PAIRS)
                    
                    # Refresh pairs periodically
                    logger.info("Scan cycle complete, refreshing pairs...")
                    pairs = self.pair_manager.get_usdt_pairs(force_refresh=True)
                    
                    # Send periodic status
                    self.send_status_update()
                    
                    # Wait before next full scan
                    logger.info(f"Waiting {SCAN_INTERVAL/3600:.1f} hours before next scan cycle")
                    time.sleep(SCAN_INTERVAL)
                    
                except KeyboardInterrupt:
                    logger.info("Shutting down...")
                    self.notifier.send("🛑 Bot stopped")
                    break
                    
                except Exception as e:
                    logger.error(f"Scan cycle error: {e}")
                    self.notifier.send(self.formatter.format_error(str(e)))
                    time.sleep(300)  # Wait 5 minutes on error
                    
        except Exception as e:
            logger.critical(f"Fatal error: {e}")
            self.notifier.send(self.formatter.format_error(f"Fatal: {str(e)}"))
            raise
    
    def save_signal(self, signal: Dict[str, Any]):
        """Save signal to JSON file (for Railway persistent volume)"""
        try:
            filename = "signals.json"
            signals = []
            
            # Load existing signals
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    signals = json.load(f)
            
            # Add new signal with timestamp
            signal['timestamp'] = datetime.now().isoformat()
            signals.append(signal)
            
            # Keep only last 1000 signals
            signals = signals[-1000:]
            
            # Save back
            with open(filename, 'w') as f:
                json.dump(signals, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save signal: {e}")
    
    def send_status_update(self):
        """Send periodic status update"""
        message = f"""📊 **Bot Status Update**
• Scans: {self.scan_count}
• Signals: {self.signal_count}
• Active Pairs: {len(self.pair_manager.pairs)}
• Uptime: {self.get_uptime()}

_Running on Railway_"""
        self.notifier.send(message)
    
    def get_uptime(self) -> str:
        """Calculate bot uptime"""
        uptime = time.time() - self._start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        
        if hours > 24:
            days = hours // 24
            hours = hours % 24
            return f"{days}d {hours}h"
        return f"{hours}h {minutes}m"

# ────────────────────────────────────────────────
#               RAILWAY COMPATIBILITY
# ────────────────────────────────────────────────

def health_check():
    """Simple health check endpoint for Railway"""
    import http.server
    import socketserver
    from threading import Thread
    
    PORT = int(os.getenv("PORT", "8080"))
    
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is running')
        
        def log_message(self, format, *args):
            # Suppress default logging
            pass
    
    def run_health_server():
        try:
            with socketserver.TCPServer(("", PORT), Handler) as httpd:
                logger.info(f"Health check server running on port {PORT}")
                httpd.serve_forever()
        except Exception as e:
            logger.error(f"Health check server error: {e}")
    
    # Start health check server in background thread
    health_thread = Thread(target=run_health_server, daemon=True)
    health_thread.start()

# ────────────────────────────────────────────────
#               MAIN ENTRY POINT
# ────────────────────────────────────────────────

if __name__ == "__main__":
    # Start health check for Railway
    health_check()
    
    # Create and run bot
    bot = TradingBot()
    
    try:
        bot.run()
    except Exception as e:
        logger.critical(f"Bot crashed: {e}")
        # Attempt to send crash notification
        try:
            notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
            notifier.send(f"💥 **Bot Crashed**\n{str(e)}")
        except:
            pass
        raise
