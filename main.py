import asyncio
from telegram.ext import Application
from bot import start, button_handler, handle_text

async def main():
    application = Application.builder().token("8029342172:AAEdx4O9KAYvjEJUXLeKzim5MotqMZURMOs").build()

    from telegram.ext import ApplicationBuilder, CommandHandler
from bot import start  # تابع از bot.py ایمپورت میشه

async def main():
    application = ApplicationBuilder().token("توکن رباتت").build()
    
    # ✅ این خط مهمه!
    application.add_handler(CommandHandler("start", start))
    
    print("✅ ربات تحلیل‌گر راه‌اندازی شد.")
    await application.run_polling()
