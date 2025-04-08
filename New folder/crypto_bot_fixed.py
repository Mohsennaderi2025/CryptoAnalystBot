
import asyncio
import logging
from datetime import datetime
import aiohttp
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)

# ------------------------------ تنظیمات ------------------------------
BOT_TOKEN = "YOUR_BOT_TOKEN"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_settings = {}

# ------------------------------ دریافت داده بازار ------------------------------
async def fetch_klines(symbol: str, interval: str = "15m", limit: int = 100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
            df = pd.DataFrame(data, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore"
            ])
            df["close"] = pd.to_numeric(df["close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            return df

# [CODE CONTINUES... Will be written fully to file]
