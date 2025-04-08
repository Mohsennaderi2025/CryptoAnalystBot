import asyncio
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from bot import start, button_handler, handle_text
import nest_asyncio

# ✅ رفع مشکل اجرای هم‌زمان در ویندوز / Jupyter
nest_asyncio.apply()

# ❗ توکن ربات تلگرام خود را اینجا قرار بده
BOT_TOKEN = "8029342172:AAHeOFxkGM4kmEBDzrqdZ-SQJ988QeAQhxE"

async def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # فرمان شروع
    application.add_handler(CommandHandler("start", start))

    # دکمه‌های منو و تنظیمات
    application.add_handler(CallbackQueryHandler(button_handler))

    # همه پیام‌های متنی کاربران
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ ربات تحلیل‌گر راه‌اندازی شد.")
    await application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
