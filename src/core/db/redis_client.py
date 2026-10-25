"""Асинхронный клиент оперативной памяти сессий и кэширования на базе Redis (Layer 2: Working Memory).

Архитектурная роль и применение паттернов Redis Iris:
1. Short-Term Working Memory (`session:{id}:*`):
   - Хранит оперативный контекст диалога (`meta`), последнюю ошибку выполнения инструмента (`last_error`)
     и счётчики подряд идущих падений идентичных команд (`cmd_fails`) для защиты агента от зацикливания.
   - Все ключи сессии автоматически инвалидируются через TTL (`redis_session_ttl_seconds`),
     а связанные записи обновляются атомарно за 1 сетевой RTT через `pipeline(transaction=True)`.
2. Short-to-Long Memory Promotion (`session:{id}:promotion`):
   - Когда агент успешно исправляет ранее упавшую команду, метод `mark_error_resolved` формирует
     структурированную карточку `MemoryPromotionCandidate` (симптом сбоя + команда исправления)
     для последующего промоушена из быстрой памяти Redis в долгосрочную векторную базу PostgreSQL (`memories`).
3. LangCache Lite (`cache:decision:*`, `cache:rules:*`):
   - Кэширует детерминированные решения хуков `PreToolUse` и скомпилированные бандлы правил по SHA-256 хешу,
     обеспечивая ответ за <1 мс без повторных запросов к PostgreSQL или внешнему LLM-арбитру.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, cast

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field
from redis.typing import EncodableT, FieldT

from src.core.config import (
    DEFAULT_REDIS_CACHE_TTL_SECONDS,
    DEFAULT_REDIS_SESSION_TTL_SECONDS,
)
from src.core.enums import (
    ProviderHealthStatus,
    RedisFlag,
    RedisKeySegment,
    RedisNamespace,
    SessionMetaField,
)

logger = logging.getLogger("mcp_coop.core.db.redis")

# Стандартный разделитель иерархии ключей Redis и длина 64-битного SHA-256 префикса (16 hex-символов)
REDIS_KEY_SEPARATOR: str = ":"
HASH_DIGEST_LENGTH: int = 16


class SessionLastError(BaseModel):
    """Неизменяемый слепок последней ошибки инструмента в рамках активной сессии диалога.

    Используется хуком PostToolUse для фиксации симптома сбоя и хуком Stop / PreToolUse
    для проверки того, была ли ошибка устранена на следующих шагах.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(description="Name of the tool that failed.")
    command: str = Field(description="Normalized command or input payload that triggered the failure.")
    error_text: str = Field(description="Captured error output or exception message.")
    step_idx: int = Field(ge=0, description="Sequential step index within the agent conversation.")


class MemoryPromotionCandidate(BaseModel):
    """Карточка кандидата для промоушена знания из Short-Term Redis в Long-Term PostgreSQL (`memories`).

    Реализует паттерн Redis Iris Short-to-Long Memory Promotion: связывает исходный симптом ошибки
    (`failed_command` + `error_text`) с командой, которая привела к успешному восстановлению (`recovery_command`).
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(description="Name of the tool where failure and recovery occurred.")
    failed_command: str = Field(description="Original command that caused the error.")
    error_text: str = Field(description="Original error symptom captured during failure.")
    recovery_command: str = Field(
        default="",
        description="Command or action that successfully resolved the error.",
    )
    step_idx: int = Field(ge=0, description="Conversation step index when the error was recorded.")


class ProviderCircuitState(BaseModel):
    """Неизменяемое состояние открытого прерывателя (Circuit Breaker) для провайдера Tier 2 в Redis.

    Позволяет всем воркерам ядра мгновенно пропускать провайдера, у которого закончились токены
    (`QUOTA_EXHAUSTED`), превышен RPM (`RATE_LIMITED`) или произошёл сбой (`UPSTREAM_ERROR`),
    и автоматически восстанавливать его после истечения TTL (`cooldown_seconds`).
    """

    model_config = ConfigDict(frozen=True)

    status: ProviderHealthStatus = Field(
        description="Operational degradation status that triggered the circuit breaker.",
    )
    reason: str = Field(
        description="Diagnostic explanation of the failure or quota exhaustion.",
    )
    cooldown_seconds: int = Field(
        ge=1,
        description="Duration in seconds for which the provider stays on cooldown in Redis.",
    )


def compute_command_hash(tool_name: str, command: str) -> str:
    """Вычисляет компактный детерминированный SHA-256 дайджест для пары (инструмент, команда).

    Почему нормализуем строки (`.strip()`) и берём срез `HASH_DIGEST_LENGTH`:
    - Исключает ложные промахи кэша из-за случайных пробелов на концах команды.
    - 16 hex-символов (64 бита энтропии) достаточно для отсутствия коллизий внутри одной сессии
      при сохранении компактного размера ключей в оперативной памяти Redis.
    """
    normalized_payload = f"{tool_name.strip()}{REDIS_KEY_SEPARATOR}{command.strip()}"
    return hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()[:HASH_DIGEST_LENGTH]


def compute_stack_hash(stack_tags: Sequence[str]) -> str:
    """Вычисляет инвариантный к порядку элементов SHA-256 хеш набора стековых тегов проекта.

    Почему теги приводятся к нижнему регистру, очищаются от дубликатов и сортируются:
    - Гарантирует, что `["python", "fastapi"]` и `["FastAPI", "python", "python"]`
      всегда отображаются в один и тот же ключ LangCache `cache:rules:{stack_hash}`.
    """
    canonical_tags = sorted({tag.strip().lower() for tag in stack_tags if tag.strip()})
    normalized_payload = REDIS_KEY_SEPARATOR.join(canonical_tags)
    return hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()[:HASH_DIGEST_LENGTH]


class RedisSessionStore:
    """Асинхронное хранилище оперативной памяти сессий и LangCache на базе `redis.asyncio`.

    Обеспечивает:
    - Управление жизненным циклом пула соединений Redis в рамках FastAPI `lifespan`.
    - Атомарный трекинг сбоев инструментов и защиту от бесконечных циклов (`cmd_fails`).
    - Подготовку кандидатов для промоушена опыта в долгосрочную память (`promotion`).
    - Сверхбыстрое кэширование решений хуков и бандлов правил (`decision`, `rules`).
    """

    def __init__(
        self,
        redis_url: str,
        *,
        session_ttl_seconds: int = DEFAULT_REDIS_SESSION_TTL_SECONDS,
        cache_ttl_seconds: int = DEFAULT_REDIS_CACHE_TTL_SECONDS,
    ) -> None:
        self._redis_url: str = redis_url.strip()
        self._session_ttl_seconds: int = session_ttl_seconds
        self._cache_ttl_seconds: int = cache_ttl_seconds
        self._client: aioredis.Redis | None = None

    @staticmethod
    def _session_key(conversation_id: str, segment: RedisKeySegment) -> str:
        """Собирает типизированный ключ пространства сессии `session:{conversation_id}:{segment}`."""
        return REDIS_KEY_SEPARATOR.join(
            (RedisNamespace.SESSION, conversation_id.strip(), segment)
        )

    @staticmethod
    def _cache_key(segment: RedisKeySegment, digest: str) -> str:
        """Собирает типизированный ключ пространства LangCache `cache:{segment}:{digest}`."""
        return REDIS_KEY_SEPARATOR.join((RedisNamespace.CACHE, segment, digest))

    @property
    def client(self) -> aioredis.Redis:
        """Возвращает активный клиент Redis или выбрасывает RuntimeError, если соединение не инициализировано."""
        if self._client is None:
            raise RuntimeError("Redis client is not initialized")
        return self._client

    @property
    def is_connected(self) -> bool:
        """Проверяет, создан ли экземпляр клиента Redis в текущем жизненном цикле."""
        return self._client is not None

    async def connect(self) -> None:
        """Инициализирует асинхронный клиент Redis с декодированием UTF-8 ответов и проверяет связь."""
        if self._client is not None:
            return

        client: aioredis.Redis = aioredis.Redis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        await cast(Awaitable[bool], client.ping())
        self._client = client
        logger.info("Redis async session store initialized and verified")

    async def check_health(self) -> bool:
        """Выполняет легковесный `PING` для проверки доступности Redis в эндпоинте `/health`."""
        if self._client is None:
            return False
        try:
            pong = await cast(Awaitable[bool], self._client.ping())
            return bool(pong)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Корректно закрывает пул соединений Redis при остановке ASGI-сервера."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("Redis async session store closed")

    # -------------------------------------------------------------------------
    # 1. Управление оперативной памятью сессии и анти-зацикливанием (Working Memory)
    # -------------------------------------------------------------------------

    async def init_session(self, conversation_id: str, workspace_path: str) -> None:
        """Инициализирует или освежает метаданные сессии в Redis Hash за 1 сетевой RTT."""
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        mapping: dict[FieldT, EncodableT] = {
            SessionMetaField.WORKSPACE_PATH.value: workspace_path,
            SessionMetaField.RESOLVED_ERROR.value: RedisFlag.FALSE.value,
            SessionMetaField.REFLECTION_PROMPTED.value: RedisFlag.FALSE.value,
        }
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, mapping=mapping)
            pipe.expire(meta_key, self._session_ttl_seconds)
            await pipe.execute()

    async def get_session_meta(self, conversation_id: str) -> dict[str, str]:
        """Возвращает словарь оперативных метаданных сессии (`session:{id}:meta`)."""
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        raw_meta = await cast(Awaitable[dict[str, str]], self.client.hgetall(meta_key))
        return dict(raw_meta)

    async def set_session_meta_field(
        self,
        conversation_id: str,
        field: SessionMetaField,
        value: str,
    ) -> None:
        """Обновляет отдельное поле в оперативных метаданных сессии (`session:{id}:meta`)."""
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, field, value)
            pipe.expire(meta_key, self._session_ttl_seconds)
            await pipe.execute()

    async def update_session_meta(
        self,
        conversation_id: str,
        updates: Mapping[str, str],
    ) -> None:
        """Атомарно обновляет набор полей метаданных сессии (`session:{id}:meta`) за 1 сетевой RTT."""
        if not updates:
            return
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        normalized: dict[FieldT, EncodableT] = {str(k): str(v) for k, v in updates.items()}
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, mapping=normalized)
            pipe.expire(meta_key, self._session_ttl_seconds)
            await pipe.execute()

    async def get_last_compaction_checkpoint(
        self,
        conversation_id: str,
    ) -> tuple[str, int]:
        """Возвращает последний сархивированный чекпоинт сжатия `(last_compact_hash, last_compact_step)`.

        Используется на `PreInvocation` (SPEC-003) для дедупликации `<summary>` из `transcript.jsonl`,
        чтобы не архивировать один и тот же чекпоинт повторно на каждом следующем шаге диалога.
        """
        meta = await self.get_session_meta(conversation_id)
        raw_hash_val = meta.get(SessionMetaField.LAST_COMPACT_HASH.value)
        raw_step_val = meta.get(SessionMetaField.LAST_COMPACT_STEP.value)
        raw_hash = str(raw_hash_val).strip() if raw_hash_val is not None else ""
        raw_step = str(raw_step_val).strip() if raw_step_val is not None else ""
        try:
            step_idx = max(0, int(raw_step)) if raw_step else 0
        except (TypeError, ValueError):
            step_idx = 0
        return raw_hash, step_idx

    async def record_compaction_checkpoint(
        self,
        conversation_id: str,
        checkpoint_hash: str,
        step_idx: int,
    ) -> None:
        """Атомарно фиксирует хеш и индекс шага сархивированного `<summary>` в `session:{id}:meta`."""
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        try:
            safe_step = max(0, int(step_idx))
        except (TypeError, ValueError):
            safe_step = 0
        mapping: dict[FieldT, EncodableT] = {
            SessionMetaField.LAST_COMPACT_HASH.value: str(checkpoint_hash).strip(),
            SessionMetaField.LAST_COMPACT_STEP.value: str(safe_step),
        }
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, mapping=mapping)
            pipe.expire(meta_key, self._session_ttl_seconds)
            await pipe.execute()

    async def record_tool_failure(
        self,
        conversation_id: str,
        error: SessionLastError,
    ) -> int:
        """Атомарно фиксирует сбой инструмента и увеличивает счётчик повторов команды за 1 RTT.

        Возвращает текущее число подряд идущих сбоев для данной пары `(tool_name, command)`,
        что позволяет хуку `PreToolUse` или `PostToolUse` мгновенно заблокировать зацикливание.
        """
        cmd_hash = compute_command_hash(error.tool_name, error.command)
        fails_key = self._session_key(conversation_id, RedisKeySegment.CMD_FAILS)
        last_error_key = self._session_key(conversation_id, RedisKeySegment.LAST_ERROR)
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)

        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hincrby(fails_key, cmd_hash, 1)
            pipe.expire(fails_key, self._session_ttl_seconds)
            pipe.set(
                last_error_key,
                error.model_dump_json(),
                ex=self._session_ttl_seconds,
            )
            pipe.hset(
                meta_key,
                mapping={
                    SessionMetaField.RESOLVED_ERROR: RedisFlag.FALSE,
                    SessionMetaField.REFLECTION_PROMPTED: RedisFlag.FALSE,
                },
            )
            pipe.expire(meta_key, self._session_ttl_seconds)
            results = await pipe.execute()

        return int(results[0])

    async def get_command_failure_count(
        self,
        conversation_id: str,
        tool_name: str,
        command: str,
    ) -> int:
        """Возвращает количество зарегистрированных сбоев конкретной команды в рамках сессии."""
        cmd_hash = compute_command_hash(tool_name, command)
        fails_key = self._session_key(conversation_id, RedisKeySegment.CMD_FAILS)
        raw_count = await cast(
            Awaitable[str | None],
            self.client.hget(fails_key, cmd_hash),
        )
        return int(raw_count) if raw_count is not None else 0

    async def get_last_error(self, conversation_id: str) -> SessionLastError | None:
        """Читает и валидирует последнюю ошибку сессии или возвращает None, если ошибок нет."""
        last_error_key = self._session_key(conversation_id, RedisKeySegment.LAST_ERROR)
        raw_json = await cast(Awaitable[str | None], self.client.get(last_error_key))
        if not raw_json:
            return None
        return SessionLastError.model_validate_json(raw_json)

    # -------------------------------------------------------------------------
    # 2. Промоушен памяти из оперативной в долгосрочную (Iris Memory Promotion)
    # -------------------------------------------------------------------------

    async def mark_error_resolved(
        self,
        conversation_id: str,
        recovery_command: str = "",
    ) -> MemoryPromotionCandidate | None:
        """Переводит решённую ошибку в статус кандидата на промоушен в долгосрочную память.

        Если в сессии числилась активная ошибка (`last_error`):
        1. Формирует неизменяемую карточку `MemoryPromotionCandidate`.
        2. Атомарно в пайплайне сохраняет её в `session:{id}:promotion`, очищает `last_error`,
           сбрасывает счётчик падений исправленной команды и выставляет `resolved_error = "1"` в `meta`.
        """
        last_error = await self.get_last_error(conversation_id)
        if last_error is None:
            return None

        candidate = MemoryPromotionCandidate(
            tool_name=last_error.tool_name,
            failed_command=last_error.command,
            error_text=last_error.error_text,
            recovery_command=recovery_command,
            step_idx=last_error.step_idx,
        )

        last_error_key = self._session_key(conversation_id, RedisKeySegment.LAST_ERROR)
        promotion_key = self._session_key(conversation_id, RedisKeySegment.PROMOTION)
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        fails_key = self._session_key(conversation_id, RedisKeySegment.CMD_FAILS)
        failed_cmd_hash = compute_command_hash(last_error.tool_name, last_error.command)

        async with self.client.pipeline(transaction=True) as pipe:
            pipe.set(
                promotion_key,
                candidate.model_dump_json(),
                ex=self._session_ttl_seconds,
            )
            pipe.delete(last_error_key)
            pipe.hdel(fails_key, failed_cmd_hash)
            pipe.hset(meta_key, SessionMetaField.RESOLVED_ERROR, RedisFlag.TRUE)
            pipe.expire(meta_key, self._session_ttl_seconds)
            await pipe.execute()

        return candidate

    async def get_promotion_candidate(
        self,
        conversation_id: str,
    ) -> MemoryPromotionCandidate | None:
        """Возвращает карточку кандидата для записи в таблицу `memories` PostgreSQL (если есть)."""
        promotion_key = self._session_key(conversation_id, RedisKeySegment.PROMOTION)
        raw_json = await cast(Awaitable[str | None], self.client.get(promotion_key))
        if not raw_json:
            return None
        return MemoryPromotionCandidate.model_validate_json(raw_json)

    async def mark_reflection_prompted(self, conversation_id: str) -> None:
        """Отмечает, что агенту уже было предложено зафиксировать решение через `propose_memory_update`."""
        meta_key = self._session_key(conversation_id, RedisKeySegment.META)
        promotion_key = self._session_key(conversation_id, RedisKeySegment.PROMOTION)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, SessionMetaField.REFLECTION_PROMPTED, RedisFlag.TRUE)
            pipe.expire(meta_key, self._session_ttl_seconds)
            pipe.delete(promotion_key)
            await pipe.execute()

    # -------------------------------------------------------------------------
    # 3. Быстрое кэширование решений и бандлов правил (Iris LangCache Lite)
    # -------------------------------------------------------------------------

    async def get_cached_decision(
        self,
        tool_name: str,
        command: str,
    ) -> dict[str, Any] | None:
        """Возвращает закэшированное решение хука для пары `(tool_name, command)` или None при промахе."""
        cmd_hash = compute_command_hash(tool_name, command)
        cache_key = self._cache_key(RedisKeySegment.DECISION, cmd_hash)
        raw_json = await cast(Awaitable[str | None], self.client.get(cache_key))
        if not raw_json:
            return None
        return cast(dict[str, Any], json.loads(raw_json))

    async def cache_decision(
        self,
        tool_name: str,
        command: str,
        decision_payload: Mapping[str, Any],
    ) -> None:
        """Сохраняет решение хука в LangCache с временем жизни `redis_cache_ttl_seconds`."""
        cmd_hash = compute_command_hash(tool_name, command)
        cache_key = self._cache_key(RedisKeySegment.DECISION, cmd_hash)
        serialized = json.dumps(dict(decision_payload), ensure_ascii=False)
        await self.client.set(cache_key, serialized, ex=self._cache_ttl_seconds)

    async def get_cached_rules_bundle(
        self,
        stack_tags: Sequence[str],
    ) -> dict[str, Any] | None:
        """Возвращает закэшированный бандл правил для указанного набора тегов стека."""
        stack_hash = compute_stack_hash(stack_tags)
        cache_key = self._cache_key(RedisKeySegment.RULES, stack_hash)
        raw_json = await cast(Awaitable[str | None], self.client.get(cache_key))
        if not raw_json:
            return None
        return cast(dict[str, Any], json.loads(raw_json))

    async def cache_rules_bundle(
        self,
        stack_tags: Sequence[str],
        bundle: Mapping[str, Any],
    ) -> None:
        """Сохраняет скомпилированный бандл правил по инвариантному хешу стека в LangCache."""
        stack_hash = compute_stack_hash(stack_tags)
        cache_key = self._cache_key(RedisKeySegment.RULES, stack_hash)
        serialized = json.dumps(dict(bundle), ensure_ascii=False)
        await self.client.set(cache_key, serialized, ex=self._cache_ttl_seconds)

    async def invalidate_rules_cache(
        self,
        stack_tags: Sequence[str] | None = None,
    ) -> int:
        """Инвалидирует закэшированные бандлы правил (`cache:rules:*`) после обновления компонентов `Harness²`."""
        if self._client is None:
            return 0
        pattern = self._cache_key(RedisKeySegment.RULES, "*")
        keys: list[str] = [
            str(key) async for key in self.client.scan_iter(match=pattern)
        ]
        if stack_tags:
            specific_key = self._cache_key(
                RedisKeySegment.RULES,
                compute_stack_hash(stack_tags),
            )
            if specific_key not in keys:
                keys.append(specific_key)
        if not keys:
            return 0
        deleted = await cast(Awaitable[int], self.client.delete(*keys))
        return int(deleted)

    # -------------------------------------------------------------------------
    # 4. Circuit Breaker рантайм-пула провайдеров (Tier 2 Failover State)
    # -------------------------------------------------------------------------

    async def get_provider_circuit(
        self,
        provider_name: str,
    ) -> ProviderCircuitState | None:
        """Возвращает активное состояние Circuit Breaker провайдера (`cache:provider_cb:{name}`) или None."""
        cb_key = self._cache_key(RedisKeySegment.PROVIDER_CB, provider_name.strip().lower())
        raw_json = await cast(Awaitable[str | None], self.client.get(cb_key))
        if not raw_json:
            return None
        return ProviderCircuitState.model_validate_json(raw_json)

    async def trip_provider_circuit(
        self,
        provider_name: str,
        state: ProviderCircuitState,
    ) -> None:
        """Размыкает прерыватель провайдера в Redis на время `state.cooldown_seconds`."""
        cb_key = self._cache_key(RedisKeySegment.PROVIDER_CB, provider_name.strip().lower())
        await self.client.set(
            cb_key,
            state.model_dump_json(),
            ex=state.cooldown_seconds,
        )

    async def clear_provider_circuit(self, provider_name: str) -> None:
        """Принудительно сбрасывает кулдаун Circuit Breaker для указанного провайдера."""
        cb_key = self._cache_key(RedisKeySegment.PROVIDER_CB, provider_name.strip().lower())
        await self.client.delete(cb_key)


__all__ = [
    "HASH_DIGEST_LENGTH",
    "REDIS_KEY_SEPARATOR",
    "MemoryPromotionCandidate",
    "ProviderCircuitState",
    "RedisSessionStore",
    "SessionLastError",
    "compute_command_hash",
    "compute_stack_hash",
]
