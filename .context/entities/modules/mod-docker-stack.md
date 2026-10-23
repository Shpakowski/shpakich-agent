---
type: Entity
class: Module
id: mod-docker-stack
name: Инфраструктура контейнеризации и изоляции (Docker Compose + pgvector + Redis)
status: Active
stack: [docker, postgresql, pgvector, redis]
files:
  - .gitignore
  - .dockerignore
  - docker/Dockerfile
  - docker/docker-compose.yml
depends_on:
  - "[[mod-python-core]]"
implements: []
governed_by: []
---

# Инфраструктура контейнеризации и изоляции (`mod-docker-stack`)

Модуль реализует **Слой 2 (Глобальное Docker-ядро)** архитектуры `mcp-coop-agentic-factory`, обеспечивая полную изоляцию аналитического Python-ядра, векторного хранилища PostgreSQL и оперативного кэша Redis от хост-машины разработчика.

## Архитектурная роль и состав модуля

1. **`.gitignore`**: защищает репозиторий от попадания секретов (`.env`), артефактов сборки Bun/TypeScript (`node_modules/`, `dist/`) и кэшей Python (`__pycache__/`, `.venv/`, `.pytest_cache/`).
2. **`.dockerignore`**: минимизирует контекст сборки образа `mcp-coop-core`, отсекая хостовый CLI (`src/cli`, `node_modules`), историю `.git`, локальные хуки `.agents` и документацию `*.md` (с сохранением `!README.md` для корректной сборки пакета из `pyproject.toml`).
3. **`docker/Dockerfile`**:
   - Базируется на легковесном образе `python:3.12-slim` (без тяжёлых локальных ML-фреймворков).
   - Настраивает переменные `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PIP_NO_CACHE_DIR=1`.
   - Исполняет процесс от непривилегированного системного пользователя `appuser` (UID `10001`).
   - Использует нативный `HEALTHCHECK` к `http://127.0.0.1:8787/health` через встроенный модуль `urllib.request`, избавляя образ от необходимости установки `curl` или `wget`.
4. **`docker/docker-compose.yml`**:
   - Оркеструет 3 контейнера в изолированной сети `mcp-coop-net`: `mcp-coop-core`, `mcp-coop-postgres` (`pgvector/pgvector:pg16`) и `mcp-coop-redis` (`redis:7-alpine` с AOF-персистентностью).
   - Жёстко привязывает сетевые порты к loopback-интерфейсу хоста (`127.0.0.1:8787:8787`), исключая внешний доступ из локальной сети.
   - Координирует порядок запуска через `depends_on` с условием `condition: service_healthy` на основе проверок `pg_isready` и `redis-cli ping`.

## Логи контекста (AI Traces)
- 2026-10-24: Создан инфраструктурный скелет Слой 2 (`.gitignore`, `.dockerignore`, `docker/Dockerfile`, `docker/docker-compose.yml`) по стандартам безопасности и легковесности 2026 года с подробными русскоязычными комментариями.
