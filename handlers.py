import logging
from aiogram import Router, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
import os
import aiofiles
from aiocsv import AsyncWriter

from wb_scraper import WBScraper

load_dotenv()

router = Router()
logger = logging.getLogger(__name__)

# Внутрирежимный In-Memory кэш для хранения результатов парсинга.
# Ключ: f"{user_id}:{sort_type}". Значение: list[dict] (список товаров).
# Примечание для продакшена: в реальных высоконагруженных системах данный кэш
# должен быть вынесен в Redis с настроенным TTL,
# чтобы предотвратить утечки оперативной памяти сервера.
WB_CACHE = {}

def get_inline_goods_keyboard(page: int = 1, current_sort: str = "popular", query: str = ""):
    """
    Генерирует инлайн-клавиатуру для управления выдачей товаров.
    Исключает текущую сортировку из списка доступных и формирует ряд пагинации.
    """

    builder = InlineKeyboardBuilder()

    all_sorts = {
        "popular": "По популярности 🔥",
        "rate": "По рейтингу ⭐",
        "newly": "По новинкам 🆕",
        "priceup": "По цене 📈",
        "pricedown": "По цене 📉"
    }

    # Добавляем только те кнопки сортировки, которые сейчас не активны
    for sort_code, sort_title in all_sorts.items():
        if sort_code == current_sort:
            continue

        builder.row(InlineKeyboardButton(
            text=sort_title,
            # При смене сортировки всегда сбрасываем отображение на 1-ю страницу
            callback_data=f"wb:sort:1:{sort_code}:{query}"
        ))

    # Формируем нижний навигационный ряд (пагинация)
    nav_buttons = []

    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"wb:page:{page - 1}:{current_sort}:{query}"
            )
        )

    if page < 8: # Ограничение в 8 страниц обусловлено глубиной парсинга (~80 товаров)
        nav_buttons.append(
            InlineKeyboardButton(
                text="Далее ➡️",
                callback_data=f"wb:page:{page + 1}:{current_sort}:{query}"
            )
        )

    if nav_buttons:
        builder.row(*nav_buttons)

    return builder.as_markup()


@router.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "Приветствуем!\nС помощью нашего бота Вы можете найти товары WB прямо в ТГ\n"
        "Используйте команду: /search <i>название товара</i>",
        parse_mode="html"
    )

@router.message(Command("search"))
async def search_command(message: Message):
    """
    Первичный обработчик поискового запроса. Запускает Playwright для дефолтной сортировки.
    """

    # Отсекаем команду /search (8 символов) и убираем лишние пробелы
    query = message.text[8:].strip()

    if not query:
        await message.answer("Вы забыли написать, что искать. Пример: /search нужный товар")
        return

    status_msg = await message.answer("Ищу товары на Wildberries, это может занять несколько секунд...")

    try:
        products = await WBScraper().search_products(query=query, sort_type="popular")
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Ошибка при первичном парсинге запроса {query}: {e}")
        await status_msg.edit_text("Произошла ошибка при поиске товара. Попробуйте позже.")
        return

    if not products:
        await message.answer("По вашему запросу ничего не найдено.")
        return

    cache_key = f"{message.from_user.id}:popular"
    WB_CACHE[cache_key] = products

    # Извлекаем первые 10 товаров для стартовой страницы
    cropped_products = products[0:10]
    formatted_goods = [
        f"🆔 Артикул: <a href='https://www.wildberries.ru/catalog/{item['art']}/detail.aspx'>{item['art']}</a> | {item['title']}: {item['price']} руб."
        for item in cropped_products
    ]

    response_text = "\n".join(formatted_goods)
    await message.answer(response_text, parse_mode="html", reply_markup=get_inline_goods_keyboard(query=query))

@router.callback_query(F.data.startswith("wb:"))
async def sort_and_page(callback: CallbackQuery):
    """
    Единый хэндлер для обработки пагинации и переключения сортировки.
    Использует срезы закэшированного списка для мгновенного ответа.
    """

    _, action, page, sort, query = callback.data.split(":")
    await callback.answer()
    page = int(page)
    user_id = str(callback.from_user.id)
    cache_key = f"{user_id}:{sort}"

    # Если данных с такой сортировкой нет в кэше — запускаем парсер заново под новый тип сортировки
    if cache_key not in WB_CACHE:
        loading_message = await callback.message.answer("Меняю сортировку, подожди секунду...")

        try:
            products = await WBScraper().search_products(query=query, sort_type=sort)
            await loading_message.delete()
        except Exception as e:
            logger.error(f"Ошибка парсинга при смене сортировки ({sort}): {e}")
            return

        if not products:
            await callback.message.answer("Не удалось загрузить товары с этой сортировкой.")
            return

        WB_CACHE[cache_key] = products

    products = WB_CACHE[cache_key]

    # Математический расчет среза для текущей страницы
    start = (page - 1) * 10
    end = start + 10
    cropped_products = products[start:end]

    if not cropped_products:
        await callback.message.answer("Товары закончились. Попробуйте изменить поисковый запрос.")
        return

    formatted_goods = [
        f"🆔 Артикул: <a href='https://www.wildberries.ru/catalog/{item['art']}/detail.aspx'>{item['art']}</a> | {item['title']}: {item['price']} руб."
        for item in cropped_products
    ]

    response_text = "\n".join(formatted_goods)

    # Защитная пленка: предотвращаем падение бота при попытке отправить идентичный текст/клавиатуру
    try:
        await callback.message.edit_text(
            text=response_text,
            parse_mode="html",
            reply_markup=get_inline_goods_keyboard(page=page, current_sort=sort, query=query)
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            pass  # Игнорируем безопасную ошибку Телеграма
        else:
            raise e

@router.message(Command("save"))
async def save_command(message: Message):
    """
    Обработчик поискового запроса для сохранения результата в файл. Запускает Playwright для дефолтной сортировки.
    """

    # Отсекаем команду /save (5 символов) и убираем лишние пробелы
    query = message.text[5:].strip()

    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    if not query:
        await message.answer("Вы забыли написать, что искать. Пример: /search нужный товар")
        return

    status_msg = await message.answer("Ищу товары на Wildberries, это может занять несколько секунд...")

    try:
        products = await WBScraper().search_products(query=query, sort_type="popular")
        file_name = "saved_products.csv"
        file_exists = os.path.isfile(file_name)
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Ошибка при первичном парсинге запроса {query}: {e}")
        await status_msg.edit_text("Произошла ошибка при поиске товара. Попробуйте позже.")
        return

    if not products:
        await message.answer("По вашему запросу ничего не найдено.")
        return

    async with aiofiles.open(file_name, mode="a", encoding="utf-8-sig", newline="") as f:
        writer = AsyncWriter(f, delimiter=";")

        if not file_exists:
            await writer.writerow(["Артикул", "Название", "Цена"])

        for prod in products:
            await writer.writerow([prod["art"], prod["title"], prod["price"]])

        await message.reply(f"Успешно сохранено в <code>{file_name}</code>!", parse_mode="html")
