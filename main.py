import asyncio
from telegram.ext import Application
from bot import start, button_handler, handle_text

async def main():
    application = Application.builder().token("8029342172:AAEdx4O9KAYvjEJUXLeKzim5MotqMZURMOs").build()

    application.add_handler(start)
    application.add_handler(button_handler)
    application.add_handler(handle_text)

    print("✅ ربات تحلیل‌گر راه‌اندازی شد.")
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
