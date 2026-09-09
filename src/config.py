import os


BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
WP_URL: str = os.getenv("WP_URL", "").rstrip("/")
WP_USERNAME: str = os.getenv("WP_USERNAME", "")
WP_PASSWORD: str = os.getenv("WP_PASSWORD", "")
ALLOWED_USERS: frozenset[int] = frozenset(
    int(uid) for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()
)

# Webhook — оставьте WEBHOOK_HOST пустым для режима polling
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH: str = f"/webhook/tg-to-wp/{BOT_TOKEN}"
WEBHOOK_URL: str = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""
WEB_SERVER_HOST: str = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
WEB_SERVER_PORT: int = int(os.getenv("PORT", "8443"))

USE_WEBHOOK: bool = bool(WEBHOOK_HOST)

PROXY_URL = os.getenv("PROXY_URL", None)

BOTAPI_URL = os.getenv("BOTAPI_URL", None)
BOTAPI_FILE_URL = os.getenv("BOTAPI_FILE_URL", None)
botapi_extended_limits = BOTAPI_URL is not None or BOTAPI_FILE_URL is not None