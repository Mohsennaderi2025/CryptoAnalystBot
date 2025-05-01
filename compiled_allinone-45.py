print("Starting script...")  # برای دیباگ

from ta.momentum import RSIIndicator
import pandas as pd
import numpy as np
import asyncio
import aiohttp
import json
import os
from datetime import datetime, timedelta
from io import BytesIO
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import matplotlib.pyplot as plt
from ta.trend import SMAIndicator, MACD
from ta.volatility import AverageTrueRange
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
load_dotenv()

# تنظیمات لاگینگ پیشرفته
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
error_logger = logging.getLogger("error_logger")
error_logger.setLevel(logging.ERROR)
handler = logging.FileHandler("bot_errors.log")
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
error_logger.addHandler(handler)

# تنظیمات پایه
BOT_TOKEN = os.getenv("BOT_TOKEN", "7662482140:AAHupAqKwVqHrOGT58uuKcX6lCauu0qIZN8")
DB_NAME = os.getenv("DB_NAME", "crypto_bot.db")
MAX_RETRIES = 3
REQUEST_TIMEOUT = 15

class StochasticRSIIndicator:
    def __init__(self, close, window=14, smooth1=3, smooth2=3):
        self.close = close
        self.window = window
        self.smooth1 = smooth1
        self.smooth2 = smooth2
        self._calculate()

    def _calculate(self):
        try:
            delta = self.close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.window).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.window).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))

            min_rsi = rsi.rolling(window=self.window).min()
            max_rsi = rsi.rolling(window=self.window).max()

            stochrsi = (rsi - min_rsi) / (max_rsi - min_rsi)
            stochrsi = stochrsi.clip(0, 1)

            self._stochrsi_k = stochrsi.rolling(window=self.smooth1).mean() * 100
            self._stochrsi_d = self._stochrsi_k.rolling(window=self.smooth2).mean()
        except Exception as e:
            error_logger.error(f"Error in StochasticRSI calculation: {e}")
            raise

    def stochrsi_k(self):
        return self._stochrsi_k

    def stochrsi_d(self):
        return self._stochrsi_d

class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.lock = asyncio.Lock()

    async def connect(self):
        try:
            self.conn = sqlite3.connect(DB_NAME, timeout=20, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout = 15000")
            self.conn.execute("PRAGMA synchronous = NORMAL")
            await self._init_tables()
            logger.info("Successfully connected to database")
            return True
        except sqlite3.Error as e:
            error_logger.error(f"Database connection error: {e}")
            return False

    async def _init_tables(self):
        try:
            cursor = self.conn.cursor()

            cursor.execute(
                """CREATE TABLE IF NOT EXISTS market_data
                            (timestamp DATETIME, symbol TEXT, interval TEXT,
                             open REAL, high REAL, low REAL, close REAL, volume REAL,
                             PRIMARY KEY (timestamp, symbol, interval))"""
            )

            cursor.execute(
                """CREATE TABLE IF NOT EXISTS user_settings
                            (user_id TEXT PRIMARY KEY, settings TEXT, 
                             last_updated DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )

            cursor.execute(
                """CREATE TABLE IF NOT EXISTS trades
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             user_id TEXT, timestamp DATETIME, symbol TEXT,
                             interval TEXT, signal INTEGER, price REAL,
                             tp REAL, sl REAL, size REAL, confidence REAL,
                             status TEXT DEFAULT 'open',
                             closed_at DATETIME,
                             profit_loss REAL,
                             FOREIGN KEY(user_id) REFERENCES user_settings(user_id))"""
            )

            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)"""
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)"""
            )

            self.conn.commit()
        except sqlite3.Error as e:
            error_logger.error(f"Error initializing tables: {e}")
            raise

    async def save_user_config(self, user_id, config):
        async with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """INSERT OR REPLACE INTO user_settings 
                    (user_id, settings, last_updated) 
                    VALUES (?, ?, datetime('now'))""",
                    (user_id, json.dumps(config)),
                )
                self.conn.commit()
                return True
            except (sqlite3.Error, TypeError) as e:
                error_logger.error(f"Error saving user config: {e}")
                return False

    async def load_user_config(self, user_id):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT settings FROM user_settings WHERE user_id = ?", (user_id,)
            )
            result = cursor.fetchone()
            return json.loads(result[0]) if result else None
        except (sqlite3.Error, json.JSONDecodeError) as e:
            error_logger.error(f"Error loading user config: {e}")
            return None

    async def log_trade(self, user_id, symbol, interval, signal_info):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT INTO trades
                (user_id, timestamp, symbol, interval, signal, price, 
                 tp, sl, size, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    datetime.now(),
                    symbol,
                    interval,
                    signal_info["signal"],
                    signal_info["price"],
                    signal_info["tp"],
                    signal_info["sl"],
                    signal_info["position_size"],
                    signal_info["confidence"],
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            error_logger.error(f"Error logging trade: {e}")
            return None

    async def get_daily_trades_count(self, user_id):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """SELECT COUNT(*) FROM trades 
                WHERE user_id = ? AND date(timestamp) = date('now')""",
                (user_id,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            error_logger.error(f"Error counting daily trades: {e}")
            return 0

    async def get_user_trades(self, user_id, limit=10):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """SELECT * FROM trades 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?""",
                (user_id, limit),
            )
            return cursor.fetchall()
        except sqlite3.Error as e:
            error_logger.error(f"Error fetching user trades: {e}")
            return []

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

class CryptoAnalyzer:
    def __init__(self, db_manager):
        self.db = db_manager
        self.cache = {}
        self.session = None
        self.cache_lock = asyncio.Lock()

    async def initialize(self):
        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                connector=aiohttp.TCPConnector(limit=10),
            )
            logger.info("CryptoAnalyzer initialized")
        except Exception as e:
            error_logger.error(f"Error initializing CryptoAnalyzer: {e}")
            raise

    async def close(self):
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                logger.info("aiohttp ClientSession closed")
        except Exception as e:
            error_logger.error(f"Error closing CryptoAnalyzer: {e}")

    async def fetch_klines(self, symbol, interval="1h", limit=500, retry=0):
        cache_key = f"{symbol}:{interval}"

        async with self.cache_lock:
            if cache_key in self.cache:
                cached_time, data = self.cache[cache_key]
                if (datetime.now() - cached_time).seconds < 300:
                    return data

        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"

        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    df = pd.DataFrame(
                        data,
                        columns=[
                            "time", "open", "high", "low", "close", "volume",
                            "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore",
                        ],
                    )

                    numeric_cols = ["open", "high", "low", "close", "volume"]
                    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
                    df["time"] = pd.to_datetime(df["time"], unit="ms")

                    async with self.cache_lock:
                        self.cache[cache_key] = (datetime.now(), df)

                    return df
                elif response.status == 429 and retry < MAX_RETRIES:
                    await asyncio.sleep(2**retry)
                    return await self.fetch_klines(symbol, interval, limit, retry + 1)
                else:
                    error_logger.error(f"API error: {response.status} - {await response.text()}")
                    return None
        except Exception as e:
            error_logger.error(f"Error fetching klines: {e}")
            if retry < MAX_RETRIES:
                await asyncio.sleep(1)
                return await self.fetch_klines(symbol, interval, limit, retry + 1)
            return None

    async def fetch_top_symbols(self, limit=20, min_volume=1000000):
        url = "https://api.binance.com/api/v3/ticker/24hr"
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    symbols = [
                        x["symbol"]
                        for x in data
                        if (
                            x["symbol"].endswith("USDT")
                            and not any(
                                x["symbol"].endswith(e)
                                for e in ["BUSD", "TUSD", "USDC", "DAI", "PAX"]
                            )
                            and float(x["quoteVolume"]) >= min_volume
                        )
                    ]

                    return sorted(
                        symbols[:limit],
                        key=lambda x: next(
                            item for item in data if item["symbol"] == x
                        )["quoteVolume"],
                        reverse=True,
                    )
                else:
                    error_logger.error(f"API error: {response.status}")
                    return []
        except Exception as e:
            error_logger.error(f"Error fetching top symbols: {e}")
            return []

    def calculate_indicators(self, df, config):
        try:
            df = df.copy()
            strat = config["strategy"]

            df["ma_short"] = SMAIndicator(df["close"], window=strat["ma_short"]).sma_indicator()
            df["ma_long"] = SMAIndicator(df["close"], window=strat["ma_long"]).sma_indicator()
            df["rsi"] = RSIIndicator(df["close"], window=strat["rsi_window"]).rsi()
            stochrsi = StochasticRSIIndicator(df["close"], window=strat["stochrsi_window"])
            df["stochrsi_k"] = stochrsi.stochrsi_k()
            df["stochrsi_d"] = stochrsi.stochrsi_d()

            macd = MACD(
                df["close"],
                window_fast=strat["macd_fast"],
                window_slow=strat["macd_slow"],
                window_sign=strat["macd_signal"],
            )
            df["macd"] = macd.macd()
            df["macd_signal"] = macd.macd_signal()
            df["macd_hist"] = macd.macd_diff()

            df["volume_ma"] = df["volume"].rolling(window=20).mean()
            df["volume_osc"] = (df["volume"] - df["volume_ma"]) / df["volume_ma"]
            df["volume_spike"] = (
                df["volume"] > strat["volume_spike_multiplier"] * df["volume_ma"]
            ).astype(int)

            df["atr"] = AverageTrueRange(
                df["high"], df["low"], df["close"], window=strat["atr_window"]
            ).average_true_range()

            hl2 = (df["high"] + df["low"]) / 2
            multiplier = strat.get("super_trend_multiplier", 3)
            upper_band = hl2 + (multiplier * df["atr"])
            lower_band = hl2 - (multiplier * df["atr"])

            df["super_trend"] = np.nan
            df["trend_direction"] = 1

            for i in range(1, len(df)):
                if df["close"].iloc[i] > upper_band.iloc[i - 1]:
                    df.loc[df.index[i], "trend_direction"] = 1
                elif df["close"].iloc[i] < lower_band.iloc[i - 1]:
                    df.loc[df.index[i], "trend_direction"] = -1
                else:
                    df.loc[df.index[i], "trend_direction"] = df["trend_direction"].iloc[i - 1]

                df.loc[df.index[i], "super_trend"] = (
                    lower_band.iloc[i]
                    if df["trend_direction"].iloc[i] == 1
                    else upper_band.iloc[i]
                )

            return df
        except Exception as e:
            error_logger.error(f"Error calculating indicators: {e}")
            raise

    def detect_patterns(self, df):
        try:
            body = df["close"] - df["open"]
            shadow = df["high"] - df["low"]

            hammer_condition1 = shadow > 3 * abs(body)
            hammer_condition2 = (df["close"] - df["low"]) / shadow > 0.6
            df["hammer"] = hammer_condition1 & hammer_condition2

            prev_close = df["close"].shift(1)
            prev_open = df["open"].shift(1)

            bullish_engulfing = (
                (prev_close < prev_open)
                & (df["close"] > df["open"])
                & (df["close"] > prev_open)
                & (df["open"] < prev_close)
            )

            bearish_engulfing = (
                (prev_close > prev_open)
                & (df["close"] < df["open"])
                & (df["close"] < prev_open)
                & (df["open"] > prev_close)
            )

            df["bullish_engulfing"] = bullish_engulfing
            df["bearish_engulfing"] = bearish_engulfing

            prev2_close = df["close"].shift(2)
            prev2_open = df["open"].shift(2)

            morning_star = (
                (prev2_close < prev2_open)
                & (df["open"].shift(1) < prev2_close)
                & (df["close"].shift(1) > df["open"].shift(1))
                & (df["close"] > prev2_close)
            )
            evening_star = (
                (prev2_close > prev_open)
                & (df["open"].shift(1) > prev2_close)
                & (df["close"].shift(1) < df["open"].shift(1))
                & (df["close"] < prev2_close)
            )

            df["morning_star"] = morning_star
            df["evening_star"] = evening_star

            return df
        except Exception as e:
            error_logger.error(f"Error detecting patterns: {e}")
            raise

    def generate_signals(self, df, config):
        try:
            strat = config["strategy"]
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            buy_conditions = (
                (latest["rsi"] < strat["rsi_oversold"])
                & (latest["stochrsi_k"] > latest["stochrsi_d"])
                & (latest["macd"] > latest["macd_signal"])
                & (latest["close"] > latest["ma_short"])
                & (latest["ma_short"] > latest["ma_long"])
                & (latest["trend_direction"] == 1)
                & (latest["volume_spike"] == 1)
                & (
                    latest["bullish_engulfing"]
                    | latest["morning_star"]
                    | latest["hammer"]
                )
            )

            sell_conditions = (
                (latest["rsi"] > strat["rsi_overbought"])
                | (latest["macd"] < latest["macd_signal"])
                | (latest["trend_direction"] == -1)
                | (latest["bearish_engulfing"] | latest["evening_star"])
            )

            if buy_conditions:
                signal = 1
                confidence = min(
                    100,
                    sum(
                        [
                            15 * (latest["rsi"] <= strat["rsi_oversold"]),
                            15 * (latest["macd"] > latest["macd_signal"]),
                            15 * (latest["volume_spike"] == 1),
                            15 * (latest["trend_direction"] == 1),
                            10 * latest["hammer"],
                            10 * latest["bullish_engulfing"],
                            10 * latest["morning_star"],
                            10 * (latest["close"] > prev["close"]),
                        ]
                    ),
                )
            elif sell_conditions:
                signal = -1
                confidence = min(
                    100,
                    sum(
                        [
                            25 * (latest["rsi"] >= strat["rsi_overbought"]),
                            25 * (latest["macd"] < latest["macd_signal"]),
                            25 * (latest["trend_direction"] == -1),
                            25 * (latest["bearish_engulfing"] | latest["evening_star"]),
                        ]
                    ),
                )
            else:
                signal = 0
                confidence = 0

            if strat["use_dynamic_tp_sl"]:
                tp = latest["close"] + strat["tp_atr_multiplier"] * latest["atr"]
                sl = latest["close"] - strat["sl_atr_multiplier"] * latest["atr"]
            else:
                tp = latest["close"] * strat["fixed_tp_ratio"]
                sl = latest["close"] * strat["fixed_sl_ratio"]

            risk_amount = (
                config["risk_management"]["max_risk_per_trade"] * 10000
            )
            position_size = (
                min(
                    risk_amount / max(0.0001, (latest["close"] - sl)),
                    strat["max_position_size"],
                )
                if (latest["close"] - sl) > 0
                else 0
            )

            return {
                "signal": signal,
                "confidence": confidence,
                "price": latest["close"],
                "tp": tp,
                "sl": sl,
                "position_size": position_size,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "indicators": {
                    "rsi": latest["rsi"],
                    "macd": latest["macd"],
                    "stochrsi_k": latest["stochrsi_k"],
                    "ma_short": latest["ma_short"],
                    "ma_long": latest["ma_long"],
                },
            }
        except Exception as e:
            error_logger.error(f"Error generating signals: {e}")
            return {
                "signal": 0,
                "confidence": 0,
                "price": df.iloc[-1]["close"] if not df.empty else 0,
                "tp": 0,
                "sl": 0,
                "position_size": 0,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "indicators": {},
            }

    async def analyze_symbol(self, symbol, interval, user_id):
        config = await self.db.load_user_config(user_id)
        if not config:
            config = self.get_default_config()

        for attempt in range(MAX_RETRIES):
            try:
                df = await self.fetch_klines(symbol, interval)
                if df is None or df.empty:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    return None

                df = self.calculate_indicators(df, config)
                df = self.detect_patterns(df)
                signal_info = self.generate_signals(df, config)

                if signal_info["signal"] != 0:
                    trade_id = await self.db.log_trade(
                        user_id, symbol, interval, signal_info
                    )
                    if trade_id:
                        signal_info["trade_id"] = trade_id

                return signal_info, df
            except Exception as e:
                error_logger.error(f"Error analyzing symbol (attempt {attempt + 1}): {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
                await asyncio.sleep(1)

    def get_default_config(self):
        return {
            "strategy": {
                "rsi_window": 14,
                "stochrsi_window": 14,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "atr_window": 14,
                "ma_short": 50,
                "ma_long": 200,
                "volume_spike_multiplier": 2,
                "rsi_overbought": 70,
                "rsi_oversold": 30,
                "use_dynamic_tp_sl": True,
                "tp_atr_multiplier": 2,
                "sl_atr_multiplier": 1,
                "fixed_tp_ratio": 1.03,
                "fixed_sl_ratio": 0.97,
                "position_size": 0.01,
                "max_position_size": 0.1,
                "max_daily_trades": 5,
                "super_trend_multiplier": 3,
            },
            "timeframes": ["15m", "30m", "1h", "4h", "1d"],
            "active_timeframe": "1h",
            "risk_management": {
                "max_risk_per_trade": 0.02,
                "max_portfolio_risk": 0.05,
                "daily_loss_limit": 0.02,
            },
        }

class TelegramBot:
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.app = None
        self.user_sessions = {}
        self.is_running = False
        self.db_manager = analyzer.db  # ذخیره مرجع db_manager

    async def initialize(self):
        try:
            logger.info("Initializing TelegramBot application...")
            self.app = Application.builder().token(BOT_TOKEN).build()

            handlers = [
                CommandHandler("start", self.start_command),
                CommandHandler("help", self.help_command),
                CommandHandler("trades", self.trades_history),
                CallbackQueryHandler(self.button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler),
            ]

            for handler in handlers:
                self.app.add_handler(handler)
            logger.info("TelegramBot handlers initialized")

            await self.app.initialize()
            logger.info("TelegramBot application initialized")
        except Exception as e:
            error_logger.error(f"Error initializing TelegramBot: {e}")
            raise

    async def start(self):
        logger.info("Starting bot...")
        try:
            if self.is_running:
                logger.warning("Bot is already running!")
                return
            if not self.analyzer:
                raise ValueError("CryptoAnalyzer is not initialized")

            logger.info("Initializing CryptoAnalyzer...")
            await self.analyzer.initialize()
            logger.info("CryptoAnalyzer initialization complete")

            logger.info("Initializing TelegramBot...")
            await self.initialize()

            logger.info("Starting application...")
            await self.app.start()
            logger.info("Application started")
            self.is_running = True
            logger.info("Bot is now running")

            # استفاده از run_polling با close_loop=False
            logger.info("Starting polling...")
            await self.app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False  # جلوگیری از بستن حلقه رویداد
            )
        except Exception as e:
            error_logger.error(f"Bot startup error: {e}")
            raise
        finally:
            logger.info("Executing shutdown in start...")
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down bot...")
        try:
            if self.is_running and self.app:
                logger.info("Stopping application...")
                try:
                    await self.app.stop()
                    logger.info("Application stopped")
                except Exception as e:
                    error_logger.error(f"Error stopping application: {e}")
                logger.info("Shutting down application...")
                try:
                    await self.app.shutdown()
                    logger.info("Application shut down")
                except Exception as e:
                    error_logger.error(f"Error shutting down application: {e}")
                self.is_running = False
            if self.analyzer:
                logger.info("Closing analyzer...")
                try:
                    await self.analyzer.close()
                    logger.info("Analyzer closed")
                except Exception as e:
                    error_logger.error(f"Error closing analyzer: {e}")
            if self.db_manager:
                logger.info("Closing database connection...")
                try:
                    self.db_manager.close()
                    logger.info("Database connection closed")
                except Exception as e:
                    error_logger.error(f"Error closing database: {e}")
        except Exception as e:
            error_logger.error(f"Error during shutdown: {e}")

    async def start(self):
        logger.info("Starting bot...")
        try:
            if self.is_running:
                logger.warning("Bot is already running!")
                return
            if not self.analyzer:
                raise ValueError("CryptoAnalyzer is not initialized")
            
            await self.analyzer.initialize()
            logger.info("CryptoAnalyzer initialization complete")

            await self.initialize()
            
            await self.app.start()
            logger.info("Application started")
            self.is_running = True
            logger.info("Bot is now running")

            # استفاده از run_polling با close_loop=False
            await self.app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False  # جلوگیری از بستن حلقه رویداد
            )
        except Exception as e:
            error_logger.error(f"Bot startup error: {e}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down bot...")
        try:
            if self.is_running and self.app:
                try:
                    await self.app.stop()
                    logger.info("Application stopped")
                except Exception as e:
                    error_logger.error(f"Error stopping application: {e}")
                try:
                    await self.app.shutdown()
                    logger.info("Application shut down")
                except Exception as e:
                    error_logger.error(f"Error shutting down application: {e}")
                self.is_running = False
            if self.analyzer:
                try:
                    await self.analyzer.close()
                    logger.info("Analyzer closed")
                except Exception as e:
                    error_logger.error(f"Error closing analyzer: {e}")
            if self.db_manager:
                try:
                    self.db_manager.close()
                    logger.info("Database connection closed")
                except Exception as e:
                    error_logger.error(f"Error closing database: {e}")
        except Exception as e:
            error_logger.error(f"Error during shutdown: {e}")

    # سایر متدها (safe_reply, start_command, help_command, و غیره) بدون تغییر باقی می‌مانند
    async def safe_reply(self, update: Update, text: str, reply_markup=None):
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
        except Exception as e:
            error_logger.error(f"Error in safe_reply: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        self.user_sessions[user_id] = {"last_active": datetime.now(), "state": None}

        keyboard = [
            [InlineKeyboardButton("🔍 تحلیل تک نماد", callback_data="analyze_single")],
            [InlineKeyboardButton("📊 تحلیل گروهی", callback_data="group_analysis")],
            [InlineKeyboardButton("📈 سیگنال‌های لحظه‌ای", callback_data="live_signals")],
            [InlineKeyboardButton("📋 تاریخچه معاملات", callback_data="trades_history")],
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")],
        ]
        markup = InlineKeyboardMarkup(keyboard)

        msg = """🤖 **ربات تحلیلگر ارزهای دیجیتال**

🔹 تحلیل تکنیکال پیشرفته
🔹 سیگنال‌های کم‌ریسک
🔹 مدیریت هوشمند معاملات
🔹 پشتیبانی از چندین تایم‌فریم

لطفاً گزینه مورد نظر را انتخاب کنید:"""

        await self.safe_reply(update, msg, markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """📚 **راهنمای استفاده از ربات**

🔹 /start - شروع کار با ربات
🔹 /help - نمایش این راهنما
🔹 /trades - نمایش تاریخچه معاملات

📊 **تحلیل تکنیکال**
- تحلیل تک نماد (مثال: BTCUSDT)
- تحلیل گروهی نمادهای پرحجم
- سیگنال‌های لحظه‌ای در تایم‌فریم 15 دقیقه

⚙️ **تنظیمات**
- تغییر تایم‌فریم تحلیل
- تنظیم پارامترهای اندیکاتورها
- مدیریت ریسک و حجم معاملات

برای شروع از منوی اصلی /start را ارسال کنید."""
        await self.safe_reply(update, help_text)

    async def trades_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        trades = await self.analyzer.db.get_user_trades(user_id, limit=10)

        if not trades:
            await self.safe_reply(update, "📭 هیچ معامله‌ای ثبت نشده است.")
            return

        msg = "📋 **تاریخچه 10 معامله اخیر**\n\n"
        for trade in trades:
            status = "✅ باز" if trade[11] == "open" else "❌ بسته"
            profit = f"{trade[12]:.2f}%" if trade[12] else "N/A"
            msg += (
                f"📌 {trade[3]} | {trade[4]}\n"
                f"⏰ {trade[2]}\n"
                f"💰 قیمت: {trade[6]:.2f}\n"
                f"📊 وضعیت: {status} | سود/زیان: {profit}\n"
                f"────────────────────\n"
            )

        await self.safe_reply(update, msg)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        user_id = str(query.from_user.id)
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {"last_active": datetime.now()}

        try:
            if query.data == "analyze_single":
                await query.edit_message_text(
                    "لطفاً نماد مورد نظر را وارد کنید (مثال: BTCUSDT):"
                )
                self.user_sessions[user_id]["state"] = "awaiting_symbol"

            elif query.data == "group_analysis":
                await self.group_analysis(update, context)

            elif query.data == "live_signals":
                await self.live_signals(update, context)

            elif query.data == "trades_history":
                await self.trades_history(update, context)

            elif query.data == "settings":
                await self.show_settings(update, context)

        except Exception as e:
            error_logger.error(f"Button handler error: {e}")
            await self.handle_error(update)

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {"last_active": datetime.now()}

        state = self.user_sessions[user_id].get("state")
        text = update.message.text.strip()

        if state == "awaiting_symbol":
            symbol = text.upper()
            if not symbol.endswith("USDT"):
                await update.message.reply_text(
                    "لطفاً نماد معتبر وارد کنید (مثال: BTCUSDT)"
                )
                return

            top_symbols = await self.analyzer.fetch_top_symbols(limit=100)
            if symbol not in top_symbols:
                await update.message.reply_text(
                    "⚠️ نماد یافت نشد. لطفاً از نمادهای معتبر استفاده کنید."
                )
                return

            await self.analyze_symbol(update, context, symbol)
            self.user_sessions[user_id]["state"] = None

    async def analyze_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
        user_id = str(update.effective_user.id)
        config = await self.analyzer.db.load_user_config(user_id) or self.analyzer.get_default_config()
        timeframe = config.get("active_timeframe", "1h")

        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )

        try:
            result = await self.analyzer.analyze_symbol(symbol, timeframe, user_id)
            if not result:
                await update.message.reply_text(f"خطا در تحلیل نماد {symbol}")
                return

            signal_info, df = result

            signal_text = {
                1: "🟢 سیگنال خرید قوی",
                -1: "🔴 سیگنال فروش",
                0: "🟡 وضعیت خنثی",
            }.get(signal_info["signal"], "🟡 وضعیت نامشخص")

            msg = f"""📈 **تحلیل {symbol} | تایم‌فریم {timeframe}**

📊 قیمت فعلی: {signal_info['price']:.4f}$
📌 سیگنال: {signal_text} (اعتماد: {signal_info['confidence']:.0f}%)
💹 اندیکاتورها:
├ RSI: {signal_info['indicators'].get('rsi', 0):.1f}
├ MACD: {signal_info['indicators'].get('macd', 0):.4f}
├ MA{config['strategy']['ma_short']}: {signal_info['indicators'].get('ma_short', 0):.2f}
└ MA{config['strategy']['ma_long']}: {signal_info['indicators'].get('ma_long', 0):.2f}

🎯 اهداف:
├ TP: {signal_info['tp']:.4f}$ (+{(signal_info['tp']/signal_info['price']-1)*100:.2f}%)
└ SL: {signal_info['sl']:.4f}$ (-{(1-signal_info['sl']/signal_info['price'])*100:.2f}%)

💰 حجم پیشنهادی: {signal_info['position_size']:.4f} واحد

⏳ زمان تحلیل: {signal_info['timestamp']}"""

            try:
                fig = plt.figure(figsize=(14, 10))
                gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 1])

                ax1 = fig.add_subplot(gs[0])
                ax2 = fig.add_subplot(gs[1])
                ax3 = fig.add_subplot(gs[2])

                ax1.plot(df["time"], df["close"], label="Price", color="black", linewidth=1.5)
                ax1.plot(df["time"], df["ma_short"], label=f'MA{config["strategy"]["ma_short"]}', linestyle="--", color="blue", alpha=0.8)
                ax1.plot(df["time"], df["ma_long"], label=f'MA{config["strategy"]["ma_long"]}', linestyle="--", color="red", alpha=0.8)
                ax1.plot(df["time"], df["super_trend"], label="SuperTrend", color="green", alpha=0.6)

                if signal_info["signal"] == 1:
                    ax1.scatter(df["time"].iloc[-1], df["close"].iloc[-1], color="green", s=200, marker="^", label="Buy Signal")
                elif signal_info["signal"] == -1:
                    ax1.scatter(df["time"].iloc[-1], df["close"].iloc[-1], color="red", s=200, marker="v", label="Sell Signal")

                ax1.axhline(signal_info["tp"], color="green", linestyle=":", alpha=0.7, label="TP")
                ax1.axhline(signal_info["sl"], color="red", linestyle=":", alpha=0.7, label="SL")

                ax1.set_title(f"Price Analysis - {symbol} ({timeframe})", fontsize=12, pad=20)
                ax1.legend(loc="upper left")
                ax1.grid(True, alpha=0.3)

                ax3.plot(df["time"], df["rsi"], label="RSI", color="purple", linewidth=1.5)
                ax3.axhline(config["strategy"]["rsi_overbought"], color="red", linestyle="--", alpha=0.7)
                ax3.axhline(config["strategy"]["rsi_oversold"], color="green", linestyle="--", alpha=0.7)

                ax3_macd = ax3.twinx()
                ax3_macd.plot(df["time"], df["macd"], label="MACD", color="blue", linewidth=1)
                ax3_macd.plot(df["time"], df["macd_signal"], label="Signal", color="orange", linewidth=1)
                ax3_macd.bar(df["time"], df["macd_hist"], color=np.where(df["macd_hist"] >= 0, "green", "red"), alpha=0.3, width=0.01)

                ax3.set_title("Indicators", fontsize=10)
                ax3.legend(loc="upper left")
                ax3_macd.legend(loc="upper right")
                ax3.grid(True, alpha=0.3)

                plt.tight_layout()

                buf = BytesIO()
                plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
                buf.seek(0)
                plt.close(fig)

                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=buf,
                    caption=msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                error_logger.error(f"Error in creating advanced chart: {e}", exc_info=True)
                plt.figure(figsize=(12, 6))
                plt.plot(df["time"], df["close"], label="Price", color="blue")
                plt.title(f"Simple Price Chart - {symbol}")
                plt.grid(True)
                plt.legend()

                buf_simple = BytesIO()
                plt.savefig(buf_simple, format="png", dpi=100)
                buf_simple.seek(0)
                plt.close()

                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=buf_simple,
                    caption=f"⚠️ Simplified Chart\n\n{msg}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            error_logger.error(f"Error in analyze_symbol: {e}")
            await update.message.reply_text(f"⚠️ خطا در تحلیل نماد {symbol}: {str(e)}")
        finally:
            plt.close("all")

    async def group_analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔢 لطفاً تعداد رمزارزهایی که می‌خواهید تحلیل شوند را وارد کنید (بین ۲ تا ۱۰):"
        )

        try:
            count_message = await context.bot.wait_for_message(chat_id=update.effective_chat.id, timeout=30)
            count = int(count_message.text.strip())
            if not (2 <= count <= 10):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❗ لطفاً عددی بین ۲ تا ۱۰ وارد کنید."
                )
                return
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❗ دریافت تعداد رمزارز ناموفق بود. لطفاً دوباره تلاش کنید."
            )
            return

        config = await self.analyzer.db.load_user_config(user_id) or self.analyzer.get_default_config()
        timeframe = config.get("active_timeframe", "1h")

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🔎 در حال تحلیل {count} رمزارز برتر... لطفاً صبر کنید..."
        )

        top_symbols = await self.analyzer.fetch_top_symbols(limit=10)
        results = []

        for symbol in top_symbols:
            try:
                result = await self.analyzer.analyze_symbol(symbol, timeframe, user_id)
                if result:
                    signal_info, df = result
                    if signal_info["signal"] == 1 and signal_info["confidence"] >= 70:
                        profit_risk_ratio = (signal_info["tp"] - signal_info["price"]) / (
                            signal_info["price"] - signal_info["sl"]
                        )
                        results.append({
                            "symbol": symbol,
                            "signal_info": signal_info,
                            "df": df,
                            "profit_risk_ratio": profit_risk_ratio,
                        })
            except Exception as e:
                error_logger.error(f"Error analyzing {symbol}: {e}")
                continue

        if not results:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❗ هیچ رمزارزی با سیگنال خرید قوی یافت نشد."
            )
            return

        results = sorted(results, key=lambda x: (x["signal_info"]["confidence"], x["profit_risk_ratio"]), reverse=True)
        selected = results[:count]

        media_group = []
        summary_lines = []

        for item in selected:
            symbol = item["symbol"]
            signal_info = item["signal_info"]
            df = item["df"]

            summary_lines.append(
                f"{symbol}: اعتماد {signal_info['confidence']}٪ | قیمت {signal_info['price']:.2f}$ | TP {signal_info['tp']:.2f}$ | SL {signal_info['sl']:.2f}$ | نسبت سود/ضرر {item['profit_risk_ratio']:.2f}"
            )

            try:
                fig, ax = plt.subplots(figsize=(12, 6))
                ax.plot(df["time"], df["close"], label="Price", color="black")
                ax.plot(df["time"], df["ma_short"], label=f"MA{config['strategy']['ma_short']}", linestyle="--")
                ax.plot(df["time"], df["ma_long"], label=f"MA{config['strategy']['ma_long']}", linestyle="--")
                ax.plot(df["time"], df["super_trend"], label="SuperTrend", color="green")

                if signal_info["signal"] == 1:
                    ax.scatter(df["time"].iloc[-1], df["close"].iloc[-1], color="green", s=150, marker="^", label="Buy")

                ax.axhline(signal_info["tp"], color="green", linestyle=":", label="TP")
                ax.axhline(signal_info["sl"], color="red", linestyle=":", label="SL")

                ax.set_title(f"{symbol} Analysis")
                ax.legend()
                ax.grid(True)

                buf = BytesIO()
                plt.savefig(buf, format="png")
                buf.seek(0)
                plt.close(fig)

                media_group.append(InputMediaPhoto(media=buf, caption=f"📈 {symbol}"))
            except Exception as e:
                error_logger.error(f"Error creating chart for {symbol}: {e}")

        await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)

        summary_text = "\n".join(summary_lines)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"📋 **خلاصه سیگنال‌های تحلیل شده:**\n\n{summary_text}",
            parse_mode="Markdown"
        )

    async def live_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        config = await self.analyzer.db.load_user_config(user_id) or self.analyzer.get_default_config()
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔔 در حال بررسی سیگنال‌های لحظه‌ای... لطفاً صبر کنید"
        )

        symbols = await self.analyzer.fetch_top_symbols(limit=20)
        signals = []
        
        for symbol in symbols:
            try:
                result = await self.analyzer.analyze_symbol(symbol, "15m", user_id)
                if result and result[0]["signal"] == 1 and result[0]["confidence"] > 75:
                    signals.append((symbol, result[0]))
            except Exception as e:
                error_logger.error(f"Error in live signal for {symbol}: {e}")
                continue

        if not signals:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ در حال حاضر هیچ سیگنال خرید قوی‌ای یافت نشد."
            )
            return

        signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
        
        message = "🚀 **سیگنال‌های لحظه‌ای (15 دقیقه)**\n\n"
        for i, (symbol, signal) in enumerate(signals[:5], 1):
            message += (
                f"{i}. {symbol}\n"
                f"   💰 قیمت: {signal['price']:.4f}$\n"
                f"   📊 اعتماد: {signal['confidence']:.0f}%\n"
                f"   🎯 TP: {signal['tp']:.4f}$ (+{(signal['tp']/signal['price']-1)*100:.2f}%)\n"
                f"   ⚠️ SL: {signal['sl']:.4f}$ (-{(1-signal['sl']/signal['price'])*100:.2f}%)\n\n"
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode="Markdown"
        )

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        config = await self.analyzer.db.load_user_config(user_id) or self.analyzer.get_default_config()
        
        settings_text = f"""⚙️ **تنظیمات فعلی شما**

📊 **استراتژی تحلیل:**
- تایم‌فریم: {config.get('active_timeframe', '1h')}
- RSI: {config['strategy']['rsi_window']} دوره
- MACD: {config['strategy']['macd_fast']}/{config['strategy']['macd_slow']}/{config['strategy']['macd_signal']}
- میانگین‌های متحرک: {config['strategy']['ma_short']}/{config['strategy']['ma_long']}

📉 **مدیریت ریسک:**
- حداکثر ریسک هر معامله: {config['risk_management']['max_risk_per_trade']*100}%
- حد ضرر روزانه: {config['risk_management']['daily_loss_limit']*100}%
"""

        keyboard = [
            [InlineKeyboardButton("تغییر تایم‌فریم", callback_data="change_timeframe")],
            [InlineKeyboardButton("تنظیمات اندیکاتورها", callback_data="indicator_settings")],
            [InlineKeyboardButton("مدیریت ریسک", callback_data="risk_settings")],
            [InlineKeyboardButton("بازگشت", callback_data="back_to_main")],
        ]
        markup = InlineKeyboardMarkup(keyboard)

        await self.safe_reply(update, settings_text, markup)

    async def handle_error(self, update: Update):
        await self.safe_reply(update, "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید.")



async def main():
    logger.info("Entering main function...")
    db_manager = DatabaseManager()
    bot = None
    try:
        logger.info("Connecting to database...")
        if not await db_manager.connect():
            logger.error("Failed to connect to database. Exiting...")
            return
        logger.info("Successfully connected to database")
        
        logger.info("Creating CryptoAnalyzer...")
        analyzer = CryptoAnalyzer(db_manager)
        
        logger.info("Creating TelegramBot...")
        bot = TelegramBot(analyzer)
        
        logger.info("Starting bot...")
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
        if bot:
            logger.info("Initiating shutdown due to KeyboardInterrupt...")
            await bot.shutdown()
    except Exception as e:
        error_logger.error(f"Error in main: {e}")
        raise
    finally:
        logger.info("Main function cleanup...")
        if bot:
            logger.info("Final shutdown in main...")
            await bot.shutdown()

if __name__ == "__main__":
    import traceback
    try:
        logger.info("Starting asyncio loop...")
        asyncio.run(main())
        logger.info("Main loop completed")
    except Exception as e:
        error_logger.error(f"Error in main loop: {e}")
        print(f"Error: {e}")
        traceback.print_exc()
        raise

if __name__ == "__main__":
    import traceback
    try:
        logger.info("Starting asyncio loop...")
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()