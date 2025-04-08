
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

async def fetch_top_symbols_by_volume(limit=20):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return []
            data = await response.json()
            symbols = [x for x in data if x["symbol"].endswith("USDT") and not x["symbol"].endswith("BUSD")]
            symbols = sorted(symbols, key=lambda x: float(x["quoteVolume"]), reverse=True)
            return [x["symbol"] for x in symbols[:limit]]

# ------------------------------ اندیکاتورها و سیگنال ------------------------------
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

def score_signal_strength(df: pd.DataFrame) -> float:
    latest = df.iloc[-1]
    score = 0
    if latest['EMA50'] > latest['EMA200']: score += 1
    if latest['RSI'] < 40: score += 1
    if latest['MACD'] > latest['Signal']: score += 1
    return score

async def get_top_n_signals(interval: str = "15m", top_n: int = 4):
    top_symbols = await fetch_top_symbols_by_volume()
    results = []

    for symbol in top_symbols:
        df = await fetch_klines(symbol, interval)
        if df is None or df.empty: continue
        df = calculate_indicators(df)
        strength = score_signal_strength(df)
        results.append((symbol, strength, df))

    results = sorted(results, key=lambda x: x[1], reverse=True)
    return results[:top_n]

def allocate_budget_among_signals(results, total_budget: float):
    total_score = sum([r[1] for r in results]) or 1
    allocation = {}
    for symbol, score, df in results:
        percent = score / total_score
        amount = total_budget * percent
        allocation[symbol] = {
            "amount": round(amount, 2),
            "entry": round(df['close'].iloc[-1], 2),
            "tp": round(df['close'].iloc[-1] * 1.05, 2),
            "sl": round(df['close'].iloc[-1] * 0.97, 2)
        }
    return allocation

# ------------------------------ ربات تلگرام ------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📈 تحلیل تک‌نماد", callback_data="analyze_single")],
        [InlineKeyboardButton("📊 تحلیل گروهی + تخصیص بودجه", callback_data="group_analysis")]
    ]
    await update.message.reply_text("به ربات تحلیل‌گر خوش آمدید، گزینه مورد نظر را انتخاب کنید:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "analyze_single":
        await query.edit_message_text("✅ لطفاً نماد رمزارز مورد نظر را وارد کنید (مثلاً BTCUSDT):")
    elif query.data == "group_analysis":
        await query.edit_message_text("💰 لطفاً میزان بودجه خود را به دلار وارد کنید:")
        context.user_data["expecting_budget"] = True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if context.user_data.get("expecting_budget"):
        try:
            budget = float(text)
            context.user_data["expecting_budget"] = False
            await update.message.reply_text("⌛ در حال تحلیل رمزارزها...")
            await group_analysis_and_allocation(update, context, budget)
        except ValueError:
            await update.message.reply_text("❌ لطفاً مقدار بودجه را به‌صورت عدد وارد کنید.")
        return

    symbol = text.upper()
    interval = "15m"
    df = await fetch_klines(symbol, interval)
    if df is None or df.empty:
        await update.message.reply_text("❌ نماد نامعتبر یا اطلاعاتی موجود نیست.")
        return

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    signal = "⚪️ سیگنال خنثی"
    if latest['EMA50'] > latest['EMA200'] and latest['RSI'] < 40 and latest['MACD'] > latest['Signal']:
        signal = "🟢 سیگنال خرید"
    elif latest['EMA50'] < latest['EMA200'] and latest['RSI'] > 60 and latest['MACD'] < latest['Signal']:
        signal = "🔴 سیگنال فروش"

    msg = f"{signal} ({symbol}) - تایم‌فریم: {interval}"
"
    msg += f"📍 قیمت فعلی: {latest['close']:.2f}"
"
    msg += f"📉 EMA50: {latest['EMA50']:.2f} | EMA200: {latest['EMA200']:.2f}
"
    msg += f"📊 RSI: {latest['RSI']:.2f} | MACD: {latest['MACD']:.2f} (Signal: {latest['Signal']:.2f})
"
    msg += f"🎯 ورود: {latest['close']:.2f} | ✅ هدف: {latest['close']*1.05:.2f} | ⛔️ حد ضرر: {latest['close']*0.97:.2f}
"
    msg += f"🕒 بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await update.message.reply_text(msg)

async def group_analysis_and_allocation(update: Update, context: ContextTypes.DEFAULT_TYPE, budget: float):
    top_results = await get_top_n_signals()
    allocation = allocate_budget_among_signals(top_results, budget)

    msg = f"📊 پیشنهاد سرمایه‌گذاری بر اساس تحلیل لحظه‌ای تایم‌فریم 15m:
"
    for symbol, info in allocation.items():
        msg += f"
🟢 {symbol}
"
        msg += f"💵 مبلغ اختصاص‌یافته: ${info['amount']}
"
        msg += f"🎯 ورود: {info['entry']} | ✅ هدف: {info['tp']} | ⛔️ حد ضرر: {info['sl']}
"

    await update.message.reply_text(msg)

# ------------------------------ اجرای ربات ------------------------------
async def main():
    application = Application.builder().token("8066127657:AAE5qJ6LclrW2WFg9grsD7UW71d7iafYoag").build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ ربات با موفقیت راه‌اندازی شد.")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
