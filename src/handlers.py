import html
import logging
import re

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import botapi_extended_limits
from .middleware import AccessMiddleware, AlbumMiddleware
from .states import Post
from .utils import post_to_wp

router = Router()
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())
router.message.outer_middleware(AlbumMiddleware())
logger = logging.getLogger(__name__)

_MORE_MARKER = "###"
_MORE_TAG = "<!--more--><br>"

_TAG_RE = re.compile(r"<[^>]+>")

# Лимит getFile: 20 МБ на публичном Bot API, 2 ГБ на локальном сервере.
MAX_TG_FILE = 2 * 1024 * 1024 * 1024 if botapi_extended_limits else 20 * 1024 * 1024
_LIMIT_LABEL = "2 ГБ" if botapi_extended_limits else "20 МБ"

_PREVIEW_LIMIT = 500

HELP_TEXT = (
    "<b>Как опубликовать пост</b>\n\n"
    "Самый быстрый способ — просто пришлите сюда сообщение: "
    "текст, фото или альбом с подписью.\n"
    "Первый абзац станет заголовком, остальное — телом поста.\n\n"
    "Пошаговый режим — команда /new.\n\n"
    "<b>Что умеет бот</b>\n"
    "• Форматирование Telegram (жирный, курсив, ссылки) переносится в WordPress\n"
    "• <code>###</code> в тексте вставляет тег «Читать далее»\n"
    "• Первое фото становится обложкой, остальные — галереей\n"
    f"• Видео до {_LIMIT_LABEL} прикрепляются под текстом\n"
    "• Перед отправкой бот показывает предпросмотр\n\n"
    "<b>Команды</b>\n"
    "/new — создать пост по шагам\n"
    "/skip — пропустить необязательный шаг\n"
    "/cancel — отменить текущий пост\n"
    "/help — эта справка"
)


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _cancel_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")


def _split_title_body(html_text: str) -> tuple[str, str]:
    """Заголовок = первый абзац (до пустой строки, иначе первая строка),
    тело = остаток. Из заголовка убираем HTML-теги (WP-заголовок — текст)."""
    html_text = html_text.strip()
    if "\n\n" in html_text:
        head, body = html_text.split("\n\n", 1)
    else:
        head, _, body = html_text.partition("\n")
    title = _TAG_RE.sub("", head).strip()
    return title, body.strip()


def _plain(html_text: str) -> str:
    """HTML → читаемый текст для предпросмотра."""
    text = html_text.replace("<br>", "\n").replace("<!--more-->", "\n[Читать далее]\n")
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _media_summary(media: list[dict]) -> str:
    imgs = sum(1 for x in media if x["kind"] == "image")
    vids = len(media) - imgs
    parts = []
    if imgs:
        parts.append(f"{imgs} фото")
    if vids:
        parts.append(f"{vids} видео")
    return ", ".join(parts)


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
            f"⚠️ Пропущено видео: {skipped} шт. больше {_LIMIT_LABEL} — "
            "Bot API не отдаёт такие файлы на скачивание."
        )
    if not media:
        return False

    await state.update_data(media=media)
    return True


async def _show_preview(message: Message, state: FSMContext) -> None:
    """Показывает собранный пост и кнопки подтверждения."""
    data = await state.get_data()
    title = data.get("title", "")
    body = _plain(data.get("body", ""))
    media = data.get("media") or []

    if len(body) > _PREVIEW_LIMIT:
        body = body[:_PREVIEW_LIMIT].rstrip() + "…"

    lines = [f"<b>{html.escape(title)}</b>"]
    if body:
        lines.append("")
        lines.append(html.escape(body))
    lines.append("")
    lines.append(f"<i>Медиа: {_media_summary(media) if media else 'нет'}</i>")

    await message.answer(
        "👀 <b>Предпросмотр</b>\n\n" + "\n".join(lines),
        reply_markup=_kb(
            [InlineKeyboardButton(text="✅ Опубликовать", callback_data="pub:now")],
            [InlineKeyboardButton(text="📝 Сохранить черновик", callback_data="pub:draft")],
            [_cancel_btn()],
        ),
    )
    await state.set_state(Post.confirm)


# ---------------------------------------------------------------------------
# /start, /help, /new
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = html.escape(message.from_user.first_name or "")
    await message.answer(
        f"Привет, {name}! Я публикую посты в WordPress.\n\n"
        "Просто пришлите сообщение — текст, фото или альбом с подписью. "
        "Первый абзац станет заголовком, остальное — телом поста.\n\n"
        "Нужен пошаговый режим — /new. Подробности — /help."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Шаг 1 из 3. Введите <b>заголовок</b> поста:",
        reply_markup=_kb([_cancel_btn()]),
    )
    await state.set_state(Post.title)


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять. Пришлите пост или нажмите /new.")
        return
    await state.clear()
    await message.answer("Черновик удалён. Пришлите новый пост, когда будете готовы.")


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Черновик удалён. Пришлите новый пост, когда будете готовы.")
    await callback.answer("Отменено")


# ---------------------------------------------------------------------------
# Быстрый пост — прислали текст/медиа без активного диалога
# ---------------------------------------------------------------------------

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def quick_post_text(message: Message, state: FSMContext) -> None:
    raw = (message.html_text or message.text or "").replace(_MORE_MARKER, _MORE_TAG)
    title, body = _split_title_body(raw)
    if not title:
        await message.answer(
            "Не вижу текста для заголовка. Пришлите пост ещё раз — "
            "первая строка станет заголовком."
        )
        return

    await state.update_data(title=title, body=body, media=[])
    await _show_preview(message, state)


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
        await message.answer(
            "У медиа нет подписи. Добавьте её — первый абзац станет заголовком, "
            "остальное телом поста."
        )
        return

    await state.update_data(title=title, body=body, media=[])
    if not await _collect_media(message, state, bot, album):
        await message.answer("Не удалось получить медиа. Попробуйте отправить ещё раз.")
        return

    await _show_preview(message, state)


@router.message(StateFilter(None), F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer(
        "Не знаю такой команды. Пришлите текст поста или начните с /new. "
        "Список команд — /help."
    )


@router.message(StateFilter(None))
async def quick_post_unsupported(message: Message) -> None:
    await message.answer(
        "Такой тип сообщения я не публикую. Пришлите текст, фото или видео. "
        "Справка — /help."
    )


# ---------------------------------------------------------------------------
# Шаг 1 — Заголовок
# ---------------------------------------------------------------------------

@router.message(Post.title, F.text & ~F.text.startswith("/"))
async def get_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title:
        await message.answer("Заголовок не может быть пустым. Введите его ещё раз:")
        return

    await state.update_data(title=title, media=[])
    await message.answer(
        "Шаг 2 из 3. Введите <b>текст поста</b> — форматирование Telegram сохранится.\n"
        f"Вставьте <code>{_MORE_MARKER}</code> там, где нужен тег «Читать далее».\n\n"
        "Можно сразу приложить фото или видео, а текст поместить в подпись.",
        reply_markup=_kb([_cancel_btn()]),
    )
    await state.set_state(Post.body)


@router.message(Post.title)
async def title_wrong_type(message: Message) -> None:
    await message.answer("Заголовок — это текст. Введите его сообщением:")


# ---------------------------------------------------------------------------
# Шаг 2 — Текст поста
# ---------------------------------------------------------------------------

@router.message(Post.body, F.text & ~F.text.startswith("/"))
async def get_body(message: Message, state: FSMContext) -> None:
    raw = (message.html_text or message.text or "").replace(_MORE_MARKER, _MORE_TAG)
    await state.update_data(body=raw)

    await message.answer(
        "Шаг 3 из 3. Пришлите <b>фото или видео</b> — первое фото станет обложкой.\n"
        "Альбом тоже подойдёт. Если медиа не нужно — /skip.",
        reply_markup=_kb(
            [InlineKeyboardButton(text="⏭ Без медиа", callback_data="media:skip")],
            [_cancel_btn()],
        ),
    )
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
        await message.answer("Не удалось получить медиа. Попробуйте отправить ещё раз.")
        return

    await _show_preview(message, state)


@router.message(Post.body)
async def body_wrong_type(message: Message) -> None:
    await message.answer(
        "Жду текст поста — можно вместе с фото или видео. Выйти — /cancel."
    )


# ---------------------------------------------------------------------------
# Шаг 3 — Медиа
# ---------------------------------------------------------------------------

@router.message(Post.image, Command("skip"))
async def skip_image(message: Message, state: FSMContext) -> None:
    await state.update_data(media=[])
    await _show_preview(message, state)


@router.message(Post.image, F.photo | F.video)
async def get_image(
    message: Message,
    state: FSMContext,
    bot: Bot,
    album: list[Message] | None = None,
) -> None:
    if not await _collect_media(message, state, bot, album):
        await message.answer("Не удалось получить медиа. Попробуйте отправить ещё раз.")
        return

    await _show_preview(message, state)


@router.callback_query(Post.image, F.data == "media:skip")
async def cb_skip_image(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(media=[])
    await _show_preview(callback.message, state)
    await callback.answer()


@router.message(Post.image)
async def image_wrong_type(message: Message) -> None:
    await message.answer(
        "Нужно <b>фото</b> или <b>видео</b>, отправленное как медиа, а не файлом. "
        "Или /skip — опубликуем без медиа."
    )


# ---------------------------------------------------------------------------
# Подтверждение — публикация или черновик
# ---------------------------------------------------------------------------

async def _submit(message: Message, state: FSMContext, publish: bool) -> None:
    data = await state.get_data()
    await state.clear()

    verb = "Публикую" if publish else "Сохраняю черновик"
    status_msg = await message.answer(f"⏳ {verb}…")

    try:
        result = await post_to_wp(data, publish=publish)
    except Exception as e:
        logger.exception("Ошибка отправки поста в WordPress")
        await status_msg.edit_text(
            "❌ Не удалось отправить пост.\n"
            f"<code>{html.escape(str(e))[:300]}</code>\n\n"
            "Пост не сохранился — пришлите его ещё раз."
        )
        return

    if publish:
        await status_msg.edit_text(
            f"✅ Пост опубликован: {result['link']}\n\n"
            "Пришлите следующий, когда будете готовы."
        )
    else:
        await status_msg.edit_text(
            f"📝 Черновик сохранён: {result['link']}\n\n"
            "Опубликовать его можно из админки WordPress."
        )


@router.callback_query(Post.confirm, F.data == "pub:now")
async def publish_now(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _submit(callback.message, state, publish=True)


@router.callback_query(Post.confirm, F.data == "pub:draft")
async def publish_draft(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _submit(callback.message, state, publish=False)


@router.message(Post.confirm)
async def confirm_wrong_input(message: Message) -> None:
    await message.answer(
        "Выберите действие кнопками под предпросмотром или отправьте /cancel."
    )


# ---------------------------------------------------------------------------
# Кнопки из устаревших сообщений
# ---------------------------------------------------------------------------

@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела — начните новый пост.", show_alert=True)
