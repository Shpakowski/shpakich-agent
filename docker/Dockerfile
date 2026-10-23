# ==============================================================================
# Слой 2 (Глобальное Docker-ядро): Dockerfile для сервиса mcp-coop-core
# Собирает легковесный контейнер на базе Python 3.12 Slim с единым ASGI-сервером
# (FastAPI + FastMCP + Uvicorn) без тяжёлых локальных ML-фреймворков.
# ==============================================================================

# Используем официальный минималистичный образ Debian Bookworm + Python 3.12.
# Вариант -slim выбран вместо -alpine, так как он обеспечивает нативную поддержку
# бинарных manylinux-колёс (asyncpg, pydantic-core) без компиляции и проблем с musl.
FROM python:3.12-slim

# Метаданные образа по стандарту OCI для идентификации компонента фабрики
LABEL org.opencontainers.image.title="mcp-coop-core" \
      org.opencontainers.image.description="Global FastAPI + FastMCP Core for mcp-coop-agentic-factory" \
      org.opencontainers.image.vendor="MCP-COOP-DAO"

# Настраиваем переменные окружения рантайма Python и пакетного менеджера pip:
# - PYTHONDONTWRITEBYTECODE=1: запрещает запись .pyc файлов на диск контейнера
# - PYTHONUNBUFFERED=1: отключает буферизацию stdout/stderr для мгновенной доставки логов в Docker
# - PIP_NO_CACHE_DIR=1: отключает сохранение кэша скачанных пакетов, уменьшая размер слоя
# - PIP_DISABLE_PIP_VERSION_CHECK=1: ускоряет установку, пропуская сетевую проверку обновлений pip
# - PYTHONPATH="/app:/app/src": гарантирует разрешение импортов как через src.core, так и через core
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH="/app:/app/src"

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Создаём выделенную системную группу и непривилегированного пользователя appuser.
# Запуск процесса от non-root пользователя — критический стандарт безопасности Docker,
# защищающий хост-систему от эскалации привилегий при компрометации процесса.
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --create-home --home-dir /home/appuser appuser

# Копируем манифест зависимостей и README.md, а затем исходный код Python-ядра.
# Благодаря .dockerignore в контекст попадают только необходимые для ядра файлы.
COPY pyproject.toml README.md ./
COPY src/core ./src/core

# Устанавливаем пакет и все его зависимости из pyproject.toml в системное окружение образа,
# после чего передаём права владения директорией приложения пользователю appuser.
RUN pip install --no-cache-dir . && \
    chown -R appuser:appgroup /app

# Переключаем контекст выполнения на непривилегированного пользователя
USER appuser

# Декларируем порт 8787, на котором работают шлюз хуков FastAPI и сервер FastMCP
EXPOSE 8787

# Нативная проверка жизнеспособности (Healthcheck) контейнера:
# Используем встроенный в стандартную библиотеку Python модуль urllib.request вместо curl/wget,
# чтобы не устанавливать в slim-образ лишние системные утилиты и не увеличивать поверхность атаки.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)"]

# Запускаем асинхронный ASGI-сервер Uvicorn.
# Внутри контейнера слушаем 0.0.0.0:8787, а жёсткая привязка к 127.0.0.1 на хосте
# обеспечивается на уровне сетевого маппинга в docker-compose.yml.
CMD ["uvicorn", "src.core.main:app", "--host", "0.0.0.0", "--port", "8787"]
