import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from fake_useragent import UserAgent
import random
import os


class WBScraper:
    """
    Интерфейс для асинхронного парсинга данных с Wildberries.
    Использует Chromium под управлением Playwright для работы с динамическим JS-контентом.
    """

    def __init__(self):
        self.ua = UserAgent()
        self.proxy_url = os.getenv("PROXY")

    async def search_products(self, sort_type: str = "popular", query: str = "") -> list:
        """
        Парсит поисковую выдачу WB по заданному запросу и типу сортировки.

        Использует кастомную асинхронную обертку Stealth для Chromium, чтобы
        переопределить внутренние сигнатуры браузера еще до момента
        инициализации контекста. Это предотвращает обнаружение
        автоматизации со стороны жесткого антифрода Wildberries.
        """

        if not query:
            return []

        url = f"https://www.wildberries.ru/catalog/0/search.aspx?page=0&sort={sort_type}&search={query}"
        goods = []

        # Инициализируем Playwright строго через менеджер контекста Stealth.
        # Это необходимо, чтобы каждый создаваемый контекст и страница
        # автоматически получали патч от детекта автоматизации.
        async with Stealth().use_async(async_playwright()) as p:
            launch_kwargs = {"headless": True}
            if self.proxy_url:
                launch_kwargs["proxy"] = {"server": self.proxy_url}

            browser = await p.chromium.launch(**launch_kwargs)

            context = await browser.new_context(
                user_agent=self.ua.chrome,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                timezone_id="Europe/Moscow"
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="commit")
                await page.locator(".product-card").first.wait_for(timeout=10000)

                # Имитируем скролл для подгрузки динамического контента.
                # 3 итерации обеспечивают оптимальный баланс: загружается ~80 товаров,
                # что достаточно для 8 страниц пагинации в Telegram, и не перегружает память.
                for _ in range(3):
                    scroll_y = random.randint(800, 1100)
                    await page.evaluate(f"window.scrollBy(0, {scroll_y});")
                    await asyncio.sleep(random.uniform(1.2, 2.8))

                cards = await page.locator(".product-card").all()

                for card in cards:
                    art = await card.get_attribute("data-nm-id")
                    title = await card.locator("a.product-card__link").get_attribute("aria-label")
                    price_raw = await card.locator(".price__lower-price").inner_text()
                    price = int("".join(filter(str.isdigit, price_raw)))

                    goods.append({
                        "art": art,
                        "title": title,
                        "price": price
                    })
            except Exception as e:
                print(f"Ошибка парсинга: {e}")
            finally:
                await browser.close()

        return goods