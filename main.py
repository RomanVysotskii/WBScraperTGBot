import asyncio
import logging
from config import bot, dp
from handlers import router


async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)

    # Очищаем очередь старых сообщений и сбрасываем вебхуки для стабильного Polling
    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())