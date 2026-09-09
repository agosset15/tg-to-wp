import asyncio
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from config import ALLOWED_USERS


class AccessMiddleware(BaseMiddleware):
    """Пускает дальше только пользователей из ALLOWED_USERS.
    Остальным отвечает отказом и обрывает обработку."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id not in ALLOWED_USERS:
            if isinstance(event, Message):
                await event.answer("У вас нет доступа к публикации.")
            return
        return await handler(event, data)


class AlbumMiddleware(BaseMiddleware):
    """
    Собирает сообщения одного media-group в список и передаёт
    его первому обработчику как `album: list[Message]`.
    Все последующие сообщения той же группы молча отбрасываются.
    """

    def __init__(self, latency: float = 1.0) -> None:
        self.latency = latency
        self._cache: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        gid = event.media_group_id
        is_first = gid not in self._cache
        self._cache.setdefault(gid, []).append(event)

        if not is_first:
            # остальные сообщения группы обработает первое
            return

        # Telegram присылает части альбома отдельными апдейтами, нередко
        # с интервалом в несколько сотен мс. Ждём, пока придут все, иначе
        # альбом «схлопнется» до первого фото.
        await asyncio.sleep(self.latency)
        album = self._cache.pop(gid)
        album.sort(key=lambda m: m.message_id)
        data["album"] = album
        return await handler(event, data)
