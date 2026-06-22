from os import getenv
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession


load_dotenv()
TOKEN = getenv('BOT_TOKEN')
proxy_url = getenv('PROXY')

if proxy_url:
    session = AiohttpSession(proxy=proxy_url)
else:
    session = None

bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()