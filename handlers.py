import logging
import re
from datetime import date, time

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import botapi_extended_limits
from middleware import AccessMiddleware, AlbumMiddleware
from states import Post
from utils import post_to_wp

router = Router()
router.message.outer_middleware(AccessMiddleware())
router.message.outer_middleware(AlbumMiddleware())
logger = logging.getLogger(__name__)

_MORE_MARKER = "###"
_MORE_TAG = "<!--more--><br>"

_TAG_RE = re.compile(r"<[^>]+>")

# Лимит getFile: 20 МБ на публичном Bot API, 2 ГБ на локальном сервере.
MAX_TG_FILE = 2 * 1024 * 1024 * 1024 if botapi_extended_limits else 20 * 1024 * 1024
_LIMIT_LABEL = "2 ГБ" if botapi_extended_limits else "20 МБ"


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _split_title_body(html: str) -> tuple[str, str]:
    """Заголовок = первый абзац (до пустой строки, иначе первая строка),
    тело = остаток. Из заголовка убираем HTML-теги (WP-заголовок — текст)."""
    html = html.strip()
    if "\n\n" in html:
        head, body = html.split("\n\n", 1)
    else:
        head, _, body = html.partition("\n")
    title = _TAG_RE.sub("", head).strip()
    return title, body.strip()


async def _file_url(bot: Bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    return bot.session.api.file_url(bot.token, file.file_path)


async def _collect_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    album: list[Message] | None,
) -> bool:
    """Собирает фото/видео из сообщения или альбома в data['media'].
    Видео крупнее лимита Bot API на скачивание пропускает.
    Возвращает False, если сохранять нечего."""
    media: list[dict] = []
    skipped = 0
    for m in album or [message]:
        if m.photo:
            url = await _file_url(bot, m.photo[-1].file_id)
            media.append(
                {"url": url, "kind": "image", "filename": "image.jpg", "mime": "image/jpeg"}
            )
        elif m.video:
            v = m.video
            if v.file_size and v.file_size > MAX_TG_FILE:
                skipped += 1
                continue
            url = await _file_url(bot, v.file_id)
            media.append(
                {
                    "url": url,
                    "kind": "video",
                    "filename": v.file_name or "video.mp4",
                    "mime": v.mime_type or "video/mp4",
                }
            )

    if skipped:
        await message.answer(
            f"Пропущено видео: {skipped} шт. больше {_LIMIT_LABEL} — "
            "Bot API не отдаёт такие файлы на скачивание."
        )
    if not media:
        return False

    await state.update_data(media=media)

    imgs = sum(1 for x in media if x["kind"] == "image")
    vids = len(media) - imgs
    if len(media) > 1 or vids:
        parts = []
        if imgs:
            parts.append(f"{imgs} фото")
        if vids:
            parts.append(f"{vids} видео")
        await message.answer("Получено: " + ", ".join(parts) + " — добавлю под текстом.")
    return True


async def _ask_publish(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Когда опубликовать?",
        reply_markup=_kb(
            [InlineKeyboardButton(text="Опубликовать сейчас", callback_data="pub:now")],
            [InlineKeyboardButton(text="Запланировать", callback_data="pub:schedule")],
        ),
    )
    await state.set_state(Post.publish)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Введите <b>заголовок</b> поста.\n"
        "Для отмены отправьте /cancel в любой момент."
    )
    await state.set_state(Post.title)


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Публикация отменена.")


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Публикация отменена.")
    await callback.answer()


# ---------------------------------------------------------------------------
# Быстрый пост — прислали текст/медиа без активного диалога
# ---------------------------------------------------------------------------

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def quick_post_text(message: Message, state: FSMContext) -> None:
    raw = (message.html_text or message.text or "").replace(_MORE_MARKER, _MORE_TAG)
    title, body = _split_title_body(raw)
    if not title:
        await message.answer("Не удалось определить заголовок. Отправьте текст поста.")
        return

    await state.update_data(title=title, body=body)
    await message.answer(f"Заголовок: <b>{title}</b>")
    await _ask_publish(message, state)


@router.message(StateFilter(None), F.photo | F.video)
async def quick_post_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    album: list[Message] | None = None,
) -> None:
    # подпись Telegram прикрепляет к первому фото группы (мин. message_id)
    src = min(album, key=lambda m: m.message_id) if album else message
    raw = (src.html_text or src.caption or "").replace(_MORE_MARKER, _MORE_TAG)
    title, body = _split_title_body(raw)
    if not title:
        await message.answer("Добавьте подпись к медиа — первый абзац станет заголовком.")
        return

    await state.update_data(title=title, body=body)
    if not await _collect_media(message, state, bot, album):
        await message.answer("Не удалось получить медиа. Попробуйте ещё раз.")
        return

    await message.answer(f"Заголовок: <b>{title}</b>")
    await _ask_publish(message, state)


# ---------------------------------------------------------------------------
# Шаг 1 — Заголовок
# ---------------------------------------------------------------------------

@router.message(Post.title, F.text)
async def get_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title:
        await message.answer("Заголовок не может быть пустым. Попробуйте ещё раз:")
        return

    await state.update_data(title=title)
    await message.answer(
        "Введите <b>текст поста</b> (поддерживается HTML-разметка Telegram).\n"
        f"Используйте <code>{_MORE_MARKER}</code> для вставки тега «Читать далее»:"
    )
    await state.set_state(Post.body)


# ---------------------------------------------------------------------------
# Шаг 2 — Текст поста
# ---------------------------------------------------------------------------

@router.message(Post.body, F.text)
async def get_body(message: Message, state: FSMContext) -> None:
    raw = (message.html_text or message.text or "").replace(_MORE_MARKER, _MORE_TAG)

    await state.update_data(body=raw)

    await message.answer("Прикрепите <b>изображение</b> для обложки поста:")
    await state.set_state(Post.image)


@router.message(Post.body, F.photo | F.video)
async def get_body_with_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    album: list[Message] | None = None,
) -> None:
    """Текст поста прислали сразу с медиа/альбомом: подпись = тело поста,
    медиа запоминаем, шаг Post.image пропускаем."""
    # подпись Telegram прикрепляет к первому элементу группы (мин. message_id)
    src = min(album, key=lambda m: m.message_id) if album else message
    raw = (src.html_text or src.caption or "").replace(_MORE_MARKER, _MORE_TAG)
    await state.update_data(body=raw)

    if not await _collect_media(message, state, bot, album):
        await message.answer("Не удалось получить медиа. Попробуйте ещё раз.")
        return

    await _ask_publish(message, state)


# ---------------------------------------------------------------------------
# Шаг 5 — Изображение
# ---------------------------------------------------------------------------

@router.message(Post.image, F.photo | F.video)
async def get_image(
    message: Message,
    state: FSMContext,
    bot: Bot,
    album: list[Message] | None = None,
) -> None:
    if not await _collect_media(message, state, bot, album):
        await message.answer("Не удалось получить медиа. Попробуйте ещё раз.")
        return

    await _ask_publish(message, state)


@router.message(Post.image)
async def image_wrong_type(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте <b>фото</b> или <b>видео</b> (не файл/документ).")


# ---------------------------------------------------------------------------
# Шаг 6 — Публикация или планирование
# ---------------------------------------------------------------------------

@router.callback_query(Post.publish, F.data == "pub:now")
async def publish_now(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Публикую пост, подождите…")

    data = await state.get_data()
    await state.clear()
    await callback.answer()

    try:
        result = await post_to_wp(data, publish_now=True)
        await callback.message.answer(f"Пост опубликован: {result['link']}")
    except Exception as e:
        logger.exception("Ошибка публикации для пользователя %d", callback.from_user.id)
        await callback.message.answer(f"Ошибка публикации: {e}")


@router.callback_query(Post.publish, F.data == "pub:schedule")
async def publish_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Введите <b>дату</b> публикации в формате <code>ГГГГ-ММ-ДД</code>:"
    )
    await state.set_state(Post.schedule_date)
    await callback.answer()


# ---------------------------------------------------------------------------
# Шаг 7 — Дата
# ---------------------------------------------------------------------------

@router.message(Post.schedule_date, F.text)
async def get_schedule_date(message: Message, state: FSMContext) -> None:
    try:
        date.fromisoformat(message.text.strip())
    except ValueError:
        await message.answer(
            "Неверный формат. Введите дату в формате <code>ГГГГ-ММ-ДД</code>:"
        )
        return

    await state.update_data(schedule_date=message.text.strip())
    await message.answer(
        "Введите <b>время</b> публикации в формате <code>ЧЧ:ММ</code>:"
    )
    await state.set_state(Post.schedule_time)


# ---------------------------------------------------------------------------
# Шаг 8 — Время
# ---------------------------------------------------------------------------

@router.message(Post.schedule_time, F.text)
async def get_schedule_time(message: Message, state: FSMContext) -> None:
    try:
        time.fromisoformat(message.text.strip())
    except ValueError:
        await message.answer(
            "Неверный формат. Введите время в формате <code>ЧЧ:ММ</code>:"
        )
        return

    await state.update_data(schedule_time=message.text.strip())
    data = await state.get_data()
    await state.clear()

    await message.answer("Планирую публикацию, подождите…")
    try:
        result = await post_to_wp(data, publish_now=False)
        await message.answer(f"Пост запланирован: {result['link']}")
    except Exception as e:
        logger.exception("Ошибка планирования для пользователя %d", message.from_user.id)
        await message.answer(f"Ошибка планирования: {e}")
