import os
import json
import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
import pandas as pd
import matplotlib.pyplot as plt
from fpdf import FPDF
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

# ------------------------------ پیکربندی اولیه ------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
USER_SETTINGS_FILE = "user_settings.json"
user_settings = {}

# --------------------------- بارگذاری تنظیمات کاربران ---------------------------
def load_user_settings():
    global user_settings
    if os.path.exists(USER_SETTINGS_FILE):
        with open(USER_SETTINGS_FILE, "r") as f:
            user_settings = json.load(f)

def save_user_settings():
    with open(USER_SETTINGS_FILE, "w") as f:
        json.dump(user_settings, f, indent=2)

# ------------------------------ دریافت داده بایننس ------------------------------
async def fetch_klines(symbol: str, interval: str = "1h", limit: int = 100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
            df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume",
                                              "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore"])
            df["close"] = pd.to_numeric(df["close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            return df

# -------------------------- محاسبات اندیکاتورهای تکنیکال --------------------------
def calculate_indicators(df: pd.DataFrame):
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['EMA200'] = df['close'].ewm(span=200).mean()
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

# ------------------------------ تولید سیگنال ------------------------------
def generate_signal(df: pd.DataFrame, symbol: str, interval: str):
    latest = df.iloc[-1]
    signal = "⚪️ سیگنال خنثی"
    if latest['EMA50'] > latest['EMA200'] and latest['RSI'] < 40 and latest['MACD'] > latest['Signal']:
        signal = "🟢 سیگنال خرید"
    elif latest['EMA50'] < latest['EMA200'] and latest['RSI'] > 60 and latest['MACD'] < latest['Signal']:
        signal = "🔴 سیگنال فروش"
    entry = latest['close']
    tp = entry * 1.05
    sl = entry * 0.97
    return (f"{signal} ({symbol}) - تایم‌فریم: {interval}\n"
            f"📍 قیمت فعلی: {entry:.2f}\n"
            f"📈 EMA50: {latest['EMA50']:.2f} | EMA200: {latest['EMA200']:.2f}\n"
            f"📊 RSI: {latest['RSI']:.2f} | MACD: {latest['MACD']:.2f} (Signal: {latest['Signal']:.2f})\n"
            f"🎯 ورود: {entry:.2f} | ✅ هدف: {tp:.2f} | ⛔️ حد ضرر: {sl:.2f}\n"
            f"🕒 بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ----------------------------- منوی تعاملی اولیه -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📈 تحلیل تک‌نماد", callback_data="single")],
        [InlineKeyboardButton("📊 تحلیل گروهی", callback_data="group")],
        [InlineKeyboardButton("⚙️ تنظیم تایم‌فریم", callback_data="set_interval")]
    ]
    await update.message.reply_text("به ربات تحلیل تکنیکال خوش آمدید. لطفاً انتخاب کنید:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

# ------------------------------ کنترل پاسخ دکمه ------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "single":
        await query.edit_message_text("🔍 لطفاً نماد رمزارز موردنظر را وارد کنید (مثلاً BTCUSDT):")
    elif query.data == "group":
        await query.edit_message_text("🔄 تحلیل گروهی هنوز در حال توسعه است.")
    elif query.data == "set_interval":
        keyboard = [[InlineKeyboardButton(tf, callback_data=f"interval_{tf}")] for tf in ["15m", "30m", "1h", "4h"]]
        await query.edit_message_text("لطفاً تایم‌فریم مورد نظر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith("interval_"):
        interval = query.data.split("_")[1]
        user_settings[str(user_id)] = {"interval": interval}
        save_user_settings()
        await query.edit_message_text(f"✅ تایم‌فریم شما روی {interval} تنظیم شد. لطفاً یک نماد را وارد کنید:")

# -------------------------- کنترل پیام متنی کاربر --------------------------
async def handle_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    symbol = update.message.text.strip().upper()
    interval = user_settings.get(user_id, {}).get("interval", "1h")
    df = await fetch_klines(symbol, interval)
    if df is None or df.empty:
        await update.message.reply_text("❌ خطا در دریافت داده یا نماد نامعتبر است.")
        return
    df = calculate_indicators(df)
    signal = generate_signal(df, symbol, interval)
    await update.message.reply_text(signal)

# ------------------------------ اجرای ربات ------------------------------

async def main():
    # اگر فایل تنظیمات دارید (مثلاً user_settings.json)، اینجا لودش کنید
    # load_user_settings()  ← در صورت نیاز فعال کن

    application = Application.builder().token("8066127657:AAE5qJ6LclrW2WFg9grsD7UW71d7iafYoag").build()

    # ثبت هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_symbol))

    print("✅ ربات با موفقیت راه‌اندازی شد.")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)


import nest_asyncio
import asyncio

nest_asyncio.apply()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

import asyncio
import aiohttp
import logging
from datetime import datetime
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ------------------------- پیکربندی اولیه -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------- اندیکاتورها -------------------------
def calculate_indicators(df):
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['EMA200'] = df['close'].ewm(span=200).mean()
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

# ------------------------- دریافت داده بایننس -------------------------
async def fetch_klines(symbol: str, interval: str = "15m", limit: int = 100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
            df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume",
                                              "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore"])
            df["close"] = pd.to_numeric(df["close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            return df

# ------------------------- دریافت نمادهای برتر -------------------------
async def get_top_symbols(limit=10):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            sorted_data = sorted(data, key=lambda x: float(x["quoteVolume"]), reverse=True)
            top_symbols = [item["symbol"] for item in sorted_data if item["symbol"].endswith("USDT")]
            return top_symbols[:limit]

# ------------------------- امتیازدهی سیگنال -------------------------
def score_signal(df):
    latest = df.iloc[-1]
    score = 0
    if latest['EMA50'] > latest['EMA200']: score += 1
    if latest['RSI'] < 40: score += 1
    if latest['MACD'] > latest['Signal']: score += 1
    return score

# ------------------------- تخصیص بودجه -------------------------
def allocate_budget(symbols_scores, total_budget):
    total_score = sum(score for _, score in symbols_scores)
    allocations = []
    for symbol, score in symbols_scores:
        percent = score / total_score if total_score else 0
        amount = percent * total_budget
        allocations.append((symbol, round(amount, 2), percent * 100))
    return allocations

# ------------------------- مدیریت درخواست کاربر -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 به ربات تحلیل‌گر خوش آمدید! برای شروع، دستور /portfolio را وارد کنید.")

async def handle_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 لطفاً مقدار سرمایه دلاری خود را وارد کنید (مثلاً 1000):")
    context.user_data["awaiting_budget"] = True

async def receive_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_budget"):
        return

    try:
        budget = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ عدد وارد شده معتبر نیست. لطفاً فقط عدد بنویسید.")
        return

    context.user_data["awaiting_budget"] = False
    interval = "15m"
    top_symbols = await get_top_symbols()
    scored_symbols = []

    for symbol in top_symbols:
        df = await fetch_klines(symbol, interval)
        if df is None or df.empty:
            continue
        df = calculate_indicators(df)
        score = score_signal(df)
        if score > 0:
            scored_symbols.append((symbol, score))

    top4 = sorted(scored_symbols, key=lambda x: x[1], reverse=True)[:4]
    allocations = allocate_budget(top4, budget)

    response = "\n📊 **پیشنهاد سرمایه‌گذاری بر اساس تحلیل تکنیکال:**\n\n"
    for symbol, amount, percent in allocations:
        response += f"🔹 {symbol}: {amount:.2f}$ ({percent:.1f}%)\n"

    response += f"\n🕒 تایم‌فریم تحلیل: {interval} | سرمایه وارد شده: {budget}$"
    await update.message.reply_text(response)

# ------------------------- اجرای برنامه -------------------------
async def main():
    application = Application.builder().token("8066127657:AAE5qJ6LclrW2WFg9grsD7UW71d7iafYoag").build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", handle_portfolio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_budget))

    print("✅ ربات با موفقیت راه‌اندازی شد.")
    await application.run_polling()

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
