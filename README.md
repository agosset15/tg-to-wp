# Telegram Bot для публикации в WordPress

Бот создаёт посты в WordPress прямо из Telegram. Пришлите сообщение с текстом и медиа — бот покажет предпросмотр и опубликует пост либо сохранит его черновиком. Поддерживаются форматирование Telegram, галереи из нескольких фото и видео.

---

## Требования

- **Python** 3.12 или выше
- **uv** — менеджер пакетов ([установка](https://docs.astral.sh/uv/getting-started/installation/))
- WordPress с включённым REST API и установленным плагином [Application Passwords](https://wordpress.org/plugins/application-passwords/) (либо Basic Auth)

### Зависимости (устанавливаются автоматически через `uv`)

| Пакет | Назначение |
|---|---|
| `aiogram >= 3.28` | Telegram Bot API, FSM |
| `httpx >= 0.28` | Асинхронные HTTP-запросы к WordPress |
| `cachetools >= 7.1` | Кэш категорий (TTL 1 час) |
| `python-dotenv >= 1.2` | Загрузка переменных окружения из `.env` |

---

## Установка

```bash
git clone https://github.com/agosset15/Telegram-to-WordPress.git
cd Telegram-to-WordPress
uv sync
```

---

## Настройка

Создайте файл `.env` в корне проекта:

```env
# Обязательные
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
WP_URL=https://your-wordpress-site.com
WP_USERNAME=your_wordpress_username
WP_PASSWORD=your_wordpress_app_password
ALLOWED_USERS=111111111,222222222

# Webhook (опционально — оставьте пустым для режима polling)
WEBHOOK_HOST=https://your-domain.com
WEB_SERVER_HOST=0.0.0.0
PORT=8443
```

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `WP_URL` | URL WordPress-сайта без завершающего слэша |
| `WP_USERNAME` | Имя пользователя WordPress |
| `WP_PASSWORD` | Пароль приложения WordPress (Application Password) |
| `ALLOWED_USERS` | ID Telegram-пользователей через запятую |
| `WEBHOOK_HOST` | Публичный HTTPS-домен для webhook; если пусто — polling |
| `WEB_SERVER_HOST` | Адрес веб-сервера (по умолчанию `0.0.0.0`) |
| `PORT` | Порт веб-сервера (по умолчанию `8443`) |

---

## Запуск

```bash
uv run python -m src
```

При пустом `WEBHOOK_HOST` бот запускается в режиме **polling**.  
При заполненном `WEBHOOK_HOST` — в режиме **webhook** (требуется действующий HTTPS-сертификат).

---

## Использование

### Команды

| Команда | Действие |
|---|---|
| `/start` | Приветствие и краткая инструкция |
| `/new` | Создать пост по шагам |
| `/skip` | Пропустить необязательный шаг (медиа) |
| `/cancel` | Отменить текущий пост |
| `/help` | Справка по возможностям бота |

### Быстрый режим

Пришлите боту обычное сообщение — текст, фото или альбом с подписью. Первый абзац
становится заголовком, остальное — телом поста. Бот покажет предпросмотр и спросит,
опубликовать пост или сохранить черновиком.

### Пошаговый режим (`/new`)

1. **Заголовок** — введите заголовок поста
2. **Текст** — введите тело поста (поддерживается HTML-форматирование Telegram)
   - Вставьте `###` в нужном месте, чтобы добавить тег «Читать далее» (`<!--more-->`)
   - Можно сразу приложить фото или видео, а текст поместить в подпись
3. **Медиа** — отправьте фото или видео, либо `/skip`
   - Можно отправить **несколько фото альбомом** — первое станет обложкой, все вместе сформируют галерею Gutenberg в конце поста
   - Видео добавляются отдельными блоками под текстом
4. **Предпросмотр** — «Опубликовать» или «Сохранить черновик»

Отложенная публикация отключена: пост либо публикуется сразу, либо сохраняется
черновиком в WordPress.

---

## Структура проекта

```
├── src/
│   ├── __main__.py   # Точка входа, инициализация и запуск бота
│   ├── config.py     # Загрузка переменных окружения
│   ├── handlers.py   # Обработчики команд и FSM-шагов
│   ├── states.py     # Состояния конечного автомата (FSM)
│   ├── middleware.py # AccessMiddleware и AlbumMiddleware
│   └── utils.py      # Работа с WordPress REST API
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml    # Зависимости проекта
└── .env              # Переменные окружения (не коммитить!)
```
