import asyncio
from telegram.ext import Application
from bot import start, button_handler, handle_text

async def main():
    application = Application.builder().token("8029342172:AAEdx4O9KAYvjEJUXLeKzim5MotqMZURMOs").build()

    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ ربات تحلیل‌گر راه‌اندازی شد.")
    await application.run_polling(allowed_updates=["message", "callback_query"])
