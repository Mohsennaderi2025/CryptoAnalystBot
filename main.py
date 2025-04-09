import asyncio
import nest_asyncio
from bot import start, button_handler, handle_text
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

async def main():
    application = Application.builder().token("8029342172:AAHeOFxkGM4kmEBDzrqdZ-SQJ988QeAQhxE").build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ ربات تحلیل‌گر راه‌اندازی شد.")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(main())
