import asyncio
import logging
import re
import httpx
from cachetools import TTLCache

from config import WP_URL, WP_USERNAME, WP_PASSWORD

logger = logging.getLogger(__name__)

_categories_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)


def _auth() -> httpx.BasicAuth:
    return httpx.BasicAuth(WP_USERNAME, WP_PASSWORD)


async def get_wp_categories() -> dict[str, str]:
    """Возвращает {category_id: name}, кэшируется на 1 час."""
    if "categories" in _categories_cache:
        return _categories_cache["categories"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{WP_URL}/wp-json/wp/v2/categories",
            auth=_auth(),
        )

    if resp.status_code != 200:
        logger.error("Ошибка загрузки категорий: %s", resp.text)
        return {}

    result = {str(cat["id"]): cat["name"] for cat in resp.json()}
    _categories_cache["categories"] = result
    return result


async def _get_all_tags() -> dict[str, int]:
    """Возвращает {tag_name_lower: tag_id}, обходит пагинацию."""
    all_tags: dict[str, int] = {}
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{WP_URL}/wp-json/wp/v2/tags",
                auth=_auth(),
                params={"per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                logger.error("Ошибка загрузки тегов (страница %d): %s", page, resp.text)
                break
            tags = resp.json()
            if not tags:
                break
            for tag in tags:
                all_tags[tag["name"].lower()] = tag["id"]
            page += 1

    return all_tags


async def _resolve_tag(tag_name: str, existing: dict[str, int]) -> int | None:
    """Возвращает ID тега, создаёт его если не существует."""
    normalized = tag_name.strip().lower()
    if normalized in existing:
        return existing[normalized]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{WP_URL}/wp-json/wp/v2/tags",
            auth=_auth(),
            json={"name": tag_name.strip()},
        )

    if resp.status_code == 201:
        tag_id: int = resp.json()["id"]
        logger.info("Создан тег '%s' (id=%d)", tag_name, tag_id)
        return tag_id

    if resp.status_code == 400 and "term_exists" in resp.text:
        refreshed = await _get_all_tags()
        tag_id = refreshed.get(normalized)
        if tag_id:
            return tag_id
        logger.error("Тег '%s' существует, но ID не найден", tag_name)
        return None

    logger.error("Не удалось создать тег '%s': %s", tag_name, resp.text)
    return None


async def upload_media_to_wp(
    media_url: str,
    filename: str | None = None,
    mime: str = "image/jpeg",
) -> dict | None:
    """
    Скачивает файл из Telegram CDN и загружает в WordPress.
    Возвращает {'id': int, 'source_url': str} или None при ошибке.
    """
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
    if not filename:
        filename = media_url.split("/")[-1]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", media_url) as src_resp:
                src_resp.raise_for_status()
                file_data = await src_resp.aread()

            upload_resp = await client.post(
                f"{WP_URL}/wp-json/wp/v2/media",
                auth=_auth(),
                files={"file": (filename, file_data, mime)},
            )
            upload_resp.raise_for_status()
            result = upload_resp.json()
            return {"id": result["id"], "source_url": result["source_url"]}
    except Exception as e:
        logger.error("Ошибка загрузки медиа [%s]: %s", type(e).__name__, e)
        return None


_EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_MENTION_RE = re.compile(r"(?<![\w@/.])@([A-Za-z][A-Za-z0-9_]{4,31})")


def _linkify(text: str) -> str:
    """@mention → t.me-ссылка, email → mailto. Текст внутри уже существующих
    тегов/ссылок не трогаем, чтобы не ломать разметку Telegram."""
    out: list[str] = []
    in_a = 0
    # делим по тегам, сохраняя их; преобразуем только текстовые участки
    for tok in re.split(r"(<[^>]+>)", text):
        if tok.startswith("<") and tok.endswith(">"):
            low = tok.lower()
            if low.startswith("<a") and not low.startswith("</a"):
                in_a += 1
            elif low.startswith("</a"):
                in_a = max(0, in_a - 1)
            out.append(tok)
            continue
        if in_a:  # внутри существующей ссылки — пропускаем
            out.append(tok)
            continue
        seg = _EMAIL_RE.sub(r'<a href="mailto:\1">\1</a>', tok)
        seg = _MENTION_RE.sub(r'<a href="https://t.me/\1">@\1</a>', seg)
        out.append(seg)
    return "".join(out)


def _body_to_blocks(body: str) -> str:
    """Конвертирует HTML из Telegram в блоки Gutenberg.

    Абзацы (разделённые пустой строкой) → wp:paragraph,
    одиночный перенос строки внутри абзаца → <br>,
    маркер <!--more--> → wp:more.
    """
    body = body.strip()
    if not body:
        return ""

    blocks: list[str] = []
    # сегменты вокруг тега «Читать далее»; сам тег станет отдельным блоком
    segments = re.split(r"<!--more-->(?:<br>)?", body)
    for i, segment in enumerate(segments):
        for para in re.split(r"\n{2,}", segment.strip()):
            para = para.strip()
            if not para:
                continue
            para = _linkify(para.replace("\n", "<br>"))
            blocks.append(
                f"<!-- wp:paragraph -->\n<p>{para}</p>\n<!-- /wp:paragraph -->"
            )
        if i < len(segments) - 1:
            blocks.append("<!-- wp:more -->\n<!--more-->\n<!-- /wp:more -->")

    return "\n\n".join(blocks)


def _build_gallery_block(media_items: list[dict]) -> str:
    """Строит Gutenberg-блок галереи из списка {'id', 'source_url'}."""
    inner = "".join(
        f'<!-- wp:image {{"id":{item["id"]},"sizeSlug":"large","linkDestination":"media"}} -->\n'
        f'<figure class="wp-block-image size-large">'
        f'<a href="{item["source_url"]}">'
        f'<img src="{item["source_url"]}" alt="" class="wp-image-{item["id"]}"/>'
        f'</a>'
        f'</figure>\n'
        f'<!-- /wp:image -->\n'
        for item in media_items
    )
    return (
        '<!-- wp:gallery {"linkTo":"mediafiles"} -->\n'
        '<figure class="wp-block-gallery has-nested-images columns-default is-cropped">\n'
        f'{inner}'
        '</figure>\n'
        '<!-- /wp:gallery -->'
    )


def _build_video_block(item: dict) -> str:
    """Строит Gutenberg-блок видео из {'id', 'source_url'}."""
    return (
        f'<!-- wp:video {{"id":{item["id"]}}} -->\n'
        f'<figure class="wp-block-video">'
        f'<video controls src="{item["source_url"]}"></video>'
        f'</figure>\n'
        f'<!-- /wp:video -->'
    )


async def post_to_wp(data: dict, publish_now: bool) -> dict:
    """
    Публикует или планирует пост в WordPress.

    data['media'] — список {'url','kind','filename','mime'}. Все файлы грузятся
    параллельно: первое изображение становится featured, остальные изображения —
    галереей, видео — отдельными wp:video блоками под текстом.
    Возвращает JSON-ответ API (содержит 'link', 'id' и др.).
    Бросает RuntimeError при ошибке.
    """
    media: list[dict] = data.get("media") or []
    content = _body_to_blocks(data.get("body", ""))
    featured_id: int | None = None

    if media:
        uploads = await asyncio.gather(
            *[
                upload_media_to_wp(m["url"], m.get("filename", "file"), m.get("mime", ""))
                for m in media
            ]
        )
        pairs = [(m, up) for m, up in zip(media, uploads) if up is not None]
        if not pairs:
            raise RuntimeError("Не удалось загрузить ни одного файла")

        images = [up for m, up in pairs if m["kind"] == "image"]
        videos = [up for m, up in pairs if m["kind"] == "video"]
        if images:
            featured_id = images[0]["id"]

        extra: list[str] = []
        if len(images) > 1:
            extra.append(_build_gallery_block(images))
        extra.extend(_build_video_block(v) for v in videos)
        if extra:
            content = content + "\n\n" + "\n\n".join(extra)

        logger.info("Загружено медиа: %d фото, %d видео", len(images), len(videos))

    post_data: dict = {
        "title": data.get("title", ""),
        "content": content,
        "categories": [1], # 1 — ID категории "Новости"
        "tags": [],
        "status": "publish" if publish_now else "future",
    }
    if featured_id is not None:
        post_data["featured_media"] = featured_id

    if not publish_now:
        sched_date = data.get("schedule_date", "")
        sched_time = data.get("schedule_time", "")
        if sched_date and sched_time:
            post_data["date"] = f"{sched_date}T{sched_time}:00"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{WP_URL}/wp-json/wp/v2/posts",
            auth=_auth(),
            json=post_data,
        )

    if resp.status_code == 201:
        result = resp.json()
        logger.info("Пост создан: %s", result.get("link"))
        return result

    raise RuntimeError(
        f"Ошибка создания поста [{resp.status_code}]: {resp.text[:300]}"
    )
