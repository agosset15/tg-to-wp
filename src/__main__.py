import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import (
    BOT_TOKEN,
    USE_WEBHOOK,
    WEBHOOK_PATH,
    WEBHOOK_URL,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT, PROXY_URL, BOTAPI_URL, BOTAPI_FILE_URL,
)
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _make_bot() -> Bot:
    session_kwargs: dict = {}
    if PROXY_URL:
        session_kwargs["proxy"] = PROXY_URL
    if BOTAPI_URL:
        if BOTAPI_FILE_URL:
            logging.info(f"Using custom Telegram Bot API server: {BOTAPI_URL} (file: {BOTAPI_FILE_URL})")
            session_kwargs["api"] = TelegramAPIServer(base=BOTAPI_URL, file=BOTAPI_FILE_URL)
        else:
            logging.info(f"Using custom Telegram Bot API server: {BOTAPI_URL}")
            session_kwargs["api"] = TelegramAPIServer.from_base(BOTAPI_URL)
    session = AiohttpSession(**session_kwargs) if session_kwargs else None
    return Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session
    )


def _make_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def _on_startup(bot: Bot) -> None:
    if USE_WEBHOOK:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook установлен: %s", WEBHOOK_URL)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Запуск в режиме polling")


async def _on_shutdown(bot: Bot) -> None:
    if USE_WEBHOOK:
        await bot.delete_webhook()


# ---------------------------------------------------------------------------
# Webhook mode
# ---------------------------------------------------------------------------

def create_web_app() -> web.Application:
    bot = _make_bot()
    dp = _make_dispatcher()
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


# ---------------------------------------------------------------------------
# Polling mode
# ---------------------------------------------------------------------------

async def run_polling() -> None:
    bot = _make_bot()
    dp = _make_dispatcher()
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if USE_WEBHOOK:
        logger.info(
            "Запуск в режиме webhook на %s:%d", WEB_SERVER_HOST, WEB_SERVER_PORT
        )
        web.run_app(create_web_app(), host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    else:
        asyncio.run(run_polling())
