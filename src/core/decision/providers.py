"""Рантайм-пул провайдеров второго контура принятия решений (`RuntimeProviderPool`, Tier 2).

Обеспечивает:
- Горячее управление списком LLM/SLM-провайдеров (`add / update / disable / priority`) без перезапуска ядра
  с синхронизацией в PostgreSQL (`decision_providers`).
- Каскадный Failover по приоритету с двухконтурным Circuit Breaker (Redis + локальные монотонные часы)
  при исчерпании квоты (`QUOTA_EXHAUSTED`), превышении лимитов (`RATE_LIMITED`), ошибке ключа (`AUTH_ERROR`)
  или сбое апстрима (`UPSTREAM_ERROR`).
- Унифицированную трансляцию батчей микро-вопросов (`predicate | choice | score`) в три протокольных
  диалекта: `OPENAI_DECISIONS`, `JEV_CLEF` и `OPENAI_CHAT_SCHEMA`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.db.postgres import PostgresDatabase
from src.core.db.redis_client import ProviderCircuitState, RedisSessionStore
from src.core.decision.models import (
    DecisionProviderConfig,
    DecisionProviderStatusView,
    DecisionQuestion,
    NormalizedAnswer,
    ProviderAttempt,
    ProviderPoolResult,
)
from src.core.enums import (
    DECISION_PROVIDER_DIALECTS,
    EMBEDDING_PROVIDER_DIALECTS,
    DecisionProviderDialect,
    DecisionQuestionType,
    EmbeddingDialect,
    ProviderHealthStatus,
)

logger = logging.getLogger("mcp_coop.core.decision.providers")

DEFAULT_OPENAI_DECISIONS_NAME: str = "openai-luna"
DEFAULT_OPENAI_DECISIONS_URL: str = "https://api.openai.com/v1/decisions"
DEFAULT_OPENAI_DECISIONS_MODEL: str = "gpt-6-luna"
DEFAULT_ENV_EMBEDDING_PROVIDER_NAME: str = "env-embedding"
DEFAULT_PRIMARY_PRIORITY: int = 10

QUOTA_COOLDOWN_SECONDS: int = 1800
RATE_LIMIT_COOLDOWN_SECONDS: int = 45
AUTH_ERROR_COOLDOWN_SECONDS: int = 3600
UPSTREAM_ERROR_COOLDOWN_SECONDS: int = 60
MS_PER_SECOND: float = 1000.0


@dataclass
class _LocalCircuitEntry:
    """Локальная запись Circuit Breaker по монотонным часам процесса (фолбэк при недоступности Redis)."""

    status: ProviderHealthStatus
    reason: str
    expires_at_monotonic: float


class RuntimeProviderPool:
    """Менеджер рантайм-пула провайдеров Tier 2 с каскадным переключением и Circuit Breaker."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._providers: dict[str, DecisionProviderConfig] = {}
        self._local_circuits: dict[str, _LocalCircuitEntry] = {}

    async def sync_from_db(self) -> int:
        """Загружает сохранённые конфигурации провайдеров из таблицы `decision_providers` в память."""
        if self._db is None or not self._db.is_connected:
            return 0

        rows = await self._db.pool.fetch(
            """
            SELECT name, dialect, base_url, model, api_key, priority, timeout_ms, enabled, metadata
            FROM decision_providers
            ORDER BY priority ASC, name ASC
            """
        )
        loaded = 0
        for row in rows:
            raw_meta = row["metadata"]
            metadata: dict[str, Any] = (
                json.loads(raw_meta)
                if isinstance(raw_meta, str)
                else dict(raw_meta or {})
            )
            cfg = DecisionProviderConfig(
                name=str(row["name"]),
                dialect=DecisionProviderDialect(str(row["dialect"])),
                base_url=str(row["base_url"]),
                model=str(row["model"]),
                api_key=str(row["api_key"] or ""),
                priority=int(row["priority"]),
                timeout_ms=int(row["timeout_ms"]),
                enabled=bool(row["enabled"]),
                metadata=metadata,
            )
            self._providers[cfg.name] = cfg
            loaded += 1
        return loaded

    async def seed_from_env(self, openai_api_key: str | None) -> None:
        """Регистрирует дефолтный провайдер `openai-luna`, если задан ключ окружения и он ещё не создан."""
        clean_key = (openai_api_key or "").strip()
        if not clean_key:
            return

        existing = self._providers.get(DEFAULT_OPENAI_DECISIONS_NAME)
        if existing is not None and existing.api_key:
            return

        default_config = DecisionProviderConfig(
            name=DEFAULT_OPENAI_DECISIONS_NAME,
            dialect=DecisionProviderDialect.OPENAI_DECISIONS,
            base_url=DEFAULT_OPENAI_DECISIONS_URL,
            model=DEFAULT_OPENAI_DECISIONS_MODEL,
            api_key=clean_key,
            priority=DEFAULT_PRIMARY_PRIORITY,
            enabled=True,
        )
        await self.upsert_provider(default_config)

    async def seed_embedding_from_env(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        dialect: EmbeddingDialect = EmbeddingDialect.AUTO,
        timeout_ms: int = 1500,
    ) -> None:
        """Регистрирует дефолтный провайдер эмбеддингов `env-embedding`, если задан ключ окружения и пул эмбеддингов пуст."""
        clean_key = (api_key or "").strip()
        if not clean_key:
            return

        has_any_embedding_provider = any(
            cfg.dialect in EMBEDDING_PROVIDER_DIALECTS and bool(cfg.api_key.strip())
            for cfg in self._providers.values()
        )
        if has_any_embedding_provider:
            return

        if dialect == EmbeddingDialect.GEMINI_NATIVE:
            resolved_dialect = DecisionProviderDialect.GEMINI_NATIVE
        elif dialect == EmbeddingDialect.OPENAI_COMPATIBLE:
            resolved_dialect = DecisionProviderDialect.OPENAI_COMPATIBLE
        elif "generativelanguage.googleapis.com" in base_url.lower() or ":embedcontent" in base_url.lower():
            resolved_dialect = DecisionProviderDialect.GEMINI_NATIVE
        else:
            resolved_dialect = DecisionProviderDialect.OPENAI_COMPATIBLE

        default_emb_config = DecisionProviderConfig(
            name=DEFAULT_ENV_EMBEDDING_PROVIDER_NAME,
            dialect=resolved_dialect,
            base_url=base_url.strip(),
            model=model.strip(),
            api_key=clean_key,
            priority=DEFAULT_PRIMARY_PRIORITY,
            timeout_ms=max(100, min(10000, int(timeout_ms))),
            enabled=True,
        )
        await self.upsert_provider(default_emb_config)

    def get_enabled_embedding_providers(self) -> list[DecisionProviderConfig]:
        """Возвращает отсортированный по `(priority, name)` список включённых провайдеров эмбеддингов."""
        return sorted(
            (
                cfg
                for cfg in self._providers.values()
                if cfg.enabled and cfg.dialect in EMBEDDING_PROVIDER_DIALECTS
            ),
            key=lambda item: (item.priority, item.name),
        )

    async def get_active_circuit(
        self,
        provider_name: str,
    ) -> tuple[ProviderHealthStatus, str, int] | None:
        """Возвращает открытый Circuit Breaker провайдера (`status`, `reason`, `remaining_s`) или `None`."""
        return await self._get_active_circuit(provider_name)

    async def record_provider_failure(
        self,
        provider_name: str,
        exc: Exception,
    ) -> tuple[ProviderHealthStatus, int, str]:
        """Классифицирует ошибку HTTP/сети и переводит провайдер в состояние кулдауна (Redis + память)."""
        status, cooldown_s, reason = self._classify_failure(exc)
        await self._trip_circuit(provider_name, status, reason, cooldown_s)
        return status, cooldown_s, reason

    async def list_providers(self) -> list[DecisionProviderStatusView]:
        """Возвращает отсортированный по приоритету список провайдеров с актуальным статусом здоровья."""
        ordered = sorted(
            self._providers.values(),
            key=lambda item: (item.priority, item.name),
        )
        views: list[DecisionProviderStatusView] = []
        for cfg in ordered:
            views.append(await self._build_status_view(cfg))
        return views

    async def upsert_provider(
        self,
        config: DecisionProviderConfig,
    ) -> DecisionProviderStatusView:
        """Добавляет или полностью обновляет провайдера в рантайм-пуле и БД, сбрасывая его Circuit Breaker."""
        self._providers[config.name] = config
        await self._clear_circuit(config.name)
        await self._persist_provider(config)
        return await self._build_status_view(config)

    async def update_provider(
        self,
        name: str,
        *,
        priority: int | None = None,
        enabled: bool | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_ms: int | None = None,
        reset_circuit: bool = False,
    ) -> DecisionProviderStatusView | None:
        """Частично обновляет параметры существующего провайдера на лету без перезапуска контейнера."""
        current = self._providers.get(name)
        if current is None:
            return None

        updated = DecisionProviderConfig(
            name=current.name,
            dialect=current.dialect,
            base_url=base_url if base_url is not None else current.base_url,
            model=model if model is not None else current.model,
            api_key=api_key if api_key is not None else current.api_key,
            priority=priority if priority is not None else current.priority,
            timeout_ms=timeout_ms if timeout_ms is not None else current.timeout_ms,
            enabled=enabled if enabled is not None else current.enabled,
            metadata=current.metadata,
        )
        self._providers[name] = updated
        if reset_circuit or api_key is not None or enabled is True:
            await self._clear_circuit(name)
        await self._persist_provider(updated)
        return await self._build_status_view(updated)

    async def remove_provider(self, name: str) -> bool:
        """Удаляет провайдера из рантайм-пула и таблицы `decision_providers`."""
        removed = self._providers.pop(name, None)
        await self._clear_circuit(name)
        if self._db is not None and self._db.is_connected:
            await self._db.pool.execute(
                "DELETE FROM decision_providers WHERE name = $1",
                name,
            )
        return removed is not None

    async def evaluate_batch(
        self,
        evidence: dict[str, Any] | str,
        questions: Sequence[DecisionQuestion],
    ) -> ProviderPoolResult:
        """Выполняет батч микро-вопросов с каскадным переключением по приоритету провайдеров.

        Алгоритм отказоустойчивости:
        1. Сортирует включённые провайдеры по `(priority, name)`.
        2. Если у провайдера открыт Circuit Breaker (`QUOTA_EXHAUSTED`, `RATE_LIMITED`, `AUTH_ERROR`, `UPSTREAM_ERROR`),
           пропускает его и добавляет явное предупреждение в `warnings`.
        3. При HTTP/сетевом сбое классифицирует причину, размыкает Circuit Breaker в Redis и локальной памяти
           и мгновенно переключается на следующий по приоритету провайдер в пуле.
        4. Если ни один провайдер не смог ответить, возвращает `ProviderPoolResult(succeeded=False, all_providers_failed=True)`.
        """
        if not questions:
            return ProviderPoolResult(succeeded=True)

        enabled_providers = sorted(
            (
                cfg
                for cfg in self._providers.values()
                if cfg.enabled and cfg.dialect in DECISION_PROVIDER_DIALECTS
            ),
            key=lambda item: (item.priority, item.name),
        )
        if not enabled_providers:
            return ProviderPoolResult(
                succeeded=False,
                warnings=[
                    "Tier 2 provider pool has no enabled providers configured; falling back to Tier 3 heuristic arbitrator."
                ],
                all_providers_failed=True,
            )

        warnings: list[str] = []
        attempts: list[ProviderAttempt] = []

        for idx, cfg in enumerate(enabled_providers):
            circuit = await self._get_active_circuit(cfg.name)
            if circuit is not None:
                status, reason, remaining_s = circuit
                warnings.append(
                    f"Provider '{cfg.name}' ({cfg.model}) is skipped due to open circuit [{status}]: {reason} ({remaining_s}s cooldown remaining)."
                )
                attempts.append(
                    ProviderAttempt(
                        provider_name=cfg.name,
                        dialect=cfg.dialect,
                        model=cfg.model,
                        status=status,
                        latency_ms=0.0,
                        error=reason,
                    )
                )
                continue

            started = time.perf_counter()
            try:
                answers = await self._invoke_provider(cfg, evidence, questions)
                elapsed_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
                attempts.append(
                    ProviderAttempt(
                        provider_name=cfg.name,
                        dialect=cfg.dialect,
                        model=cfg.model,
                        status=ProviderHealthStatus.HEALTHY,
                        latency_ms=elapsed_ms,
                    )
                )
                return ProviderPoolResult(
                    succeeded=True,
                    provider_used=cfg.name,
                    answers=answers,
                    warnings=warnings,
                    attempts=attempts,
                    all_providers_failed=False,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
                status, cooldown_s, reason = self._classify_failure(exc)
                await self._trip_circuit(cfg.name, status, reason, cooldown_s)
                attempts.append(
                    ProviderAttempt(
                        provider_name=cfg.name,
                        dialect=cfg.dialect,
                        model=cfg.model,
                        status=status,
                        latency_ms=elapsed_ms,
                        error=reason,
                    )
                )
                next_provider = (
                    enabled_providers[idx + 1].name
                    if idx + 1 < len(enabled_providers)
                    else "Tier 3 fallback"
                )
                warnings.append(
                    f"Provider '{cfg.name}' ({cfg.model}) failed with [{status}] ({reason}); failing over to {next_provider}."
                )

        warnings.append(
            "All configured Tier 2 providers are unavailable or exhausted; escalating to Tier 3 local fallback."
        )
        return ProviderPoolResult(
            succeeded=False,
            provider_used=None,
            answers={},
            warnings=warnings,
            attempts=attempts,
            all_providers_failed=True,
        )

    async def _persist_provider(self, config: DecisionProviderConfig) -> None:
        """Сохраняет конфигурацию провайдера в таблицу `decision_providers` (если БД подключена)."""
        if self._db is None or not self._db.is_connected:
            return

        await self._db.pool.execute(
            """
            INSERT INTO decision_providers (
                name, dialect, base_url, model, api_key, priority, timeout_ms, enabled, metadata, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, NOW())
            ON CONFLICT (name) DO UPDATE SET
                dialect = EXCLUDED.dialect,
                base_url = EXCLUDED.base_url,
                model = EXCLUDED.model,
                api_key = EXCLUDED.api_key,
                priority = EXCLUDED.priority,
                timeout_ms = EXCLUDED.timeout_ms,
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            config.name,
            config.dialect.value,
            config.base_url,
            config.model,
            config.api_key,
            config.priority,
            config.timeout_ms,
            config.enabled,
            json.dumps(config.metadata, ensure_ascii=False),
        )

    async def _build_status_view(
        self,
        config: DecisionProviderConfig,
    ) -> DecisionProviderStatusView:
        """Собирает публичный срез состояния провайдера с учётом активного Circuit Breaker."""
        if not config.enabled:
            return DecisionProviderStatusView(
                name=config.name,
                dialect=config.dialect,
                base_url=config.base_url,
                model=config.model,
                api_key_configured=bool(config.api_key.strip()),
                priority=config.priority,
                timeout_ms=config.timeout_ms,
                enabled=False,
                health_status=ProviderHealthStatus.DISABLED,
                metadata=config.metadata,
            )

        circuit = await self._get_active_circuit(config.name)
        if circuit is not None:
            status, reason, remaining_s = circuit
            return DecisionProviderStatusView(
                name=config.name,
                dialect=config.dialect,
                base_url=config.base_url,
                model=config.model,
                api_key_configured=bool(config.api_key.strip()),
                priority=config.priority,
                timeout_ms=config.timeout_ms,
                enabled=True,
                health_status=status,
                circuit_reason=reason,
                cooldown_seconds_remaining=remaining_s,
                metadata=config.metadata,
            )

        return DecisionProviderStatusView(
            name=config.name,
            dialect=config.dialect,
            base_url=config.base_url,
            model=config.model,
            api_key_configured=bool(config.api_key.strip()),
            priority=config.priority,
            timeout_ms=config.timeout_ms,
            enabled=True,
            health_status=ProviderHealthStatus.HEALTHY,
            metadata=config.metadata,
        )

    async def _get_active_circuit(
        self,
        provider_name: str,
    ) -> tuple[ProviderHealthStatus, str, int] | None:
        """Проверяет открытый Circuit Breaker провайдера в локальной памяти и Redis."""
        now_mono = time.monotonic()
        local_entry = self._local_circuits.get(provider_name)
        if local_entry is not None:
            if local_entry.expires_at_monotonic > now_mono:
                remaining = max(1, int(local_entry.expires_at_monotonic - now_mono))
                return local_entry.status, local_entry.reason, remaining
            self._local_circuits.pop(provider_name, None)

        if self._redis is not None and self._redis.is_connected:
            try:
                redis_state = await self._redis.get_provider_circuit(provider_name)
                if redis_state is not None:
                    return (
                        redis_state.status,
                        redis_state.reason,
                        redis_state.cooldown_seconds,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis circuit read failed for %s: %s", provider_name, exc)

        return None

    async def _trip_circuit(
        self,
        provider_name: str,
        status: ProviderHealthStatus,
        reason: str,
        cooldown_seconds: int,
    ) -> None:
        """Размыкает прерыватель провайдера одновременно в локальной памяти и Redis."""
        self._local_circuits[provider_name] = _LocalCircuitEntry(
            status=status,
            reason=reason,
            expires_at_monotonic=time.monotonic() + float(cooldown_seconds),
        )
        if self._redis is not None and self._redis.is_connected:
            try:
                await self._redis.trip_provider_circuit(
                    provider_name,
                    ProviderCircuitState(
                        status=status,
                        reason=reason,
                        cooldown_seconds=cooldown_seconds,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis circuit trip failed for %s: %s", provider_name, exc)

    async def _clear_circuit(self, provider_name: str) -> None:
        """Сбрасывает Circuit Breaker провайдера в локальной памяти и Redis."""
        self._local_circuits.pop(provider_name, None)
        if self._redis is not None and self._redis.is_connected:
            try:
                await self._redis.clear_provider_circuit(provider_name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis circuit clear failed for %s: %s", provider_name, exc)

    @staticmethod
    def _classify_failure(
        exc: Exception,
    ) -> tuple[ProviderHealthStatus, int, str]:
        """Классифицирует ошибку вызова провайдера и определяет длительность кулдауна."""
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            body_text = exc.response.text[:300].lower()
            if code in (401, 403):
                return (
                    ProviderHealthStatus.AUTH_ERROR,
                    AUTH_ERROR_COOLDOWN_SECONDS,
                    f"HTTP {code} authentication or permission rejected",
                )
            if code == 402 or (
                code == 429
                and (
                    "insufficient_quota" in body_text
                    or "quota" in body_text
                    or "billing" in body_text
                    or "credit" in body_text
                )
            ):
                return (
                    ProviderHealthStatus.QUOTA_EXHAUSTED,
                    QUOTA_COOLDOWN_SECONDS,
                    f"HTTP {code} token quota or billing balance exhausted",
                )
            if code == 429:
                return (
                    ProviderHealthStatus.RATE_LIMITED,
                    RATE_LIMIT_COOLDOWN_SECONDS,
                    "HTTP 429 rate limit exceeded",
                )
            return (
                ProviderHealthStatus.UPSTREAM_ERROR,
                UPSTREAM_ERROR_COOLDOWN_SECONDS,
                f"HTTP {code} upstream provider error",
            )

        if isinstance(exc, httpx.TimeoutException):
            return (
                ProviderHealthStatus.UPSTREAM_ERROR,
                UPSTREAM_ERROR_COOLDOWN_SECONDS,
                "Request timed out waiting for Tier 2 provider",
            )

        return (
            ProviderHealthStatus.UPSTREAM_ERROR,
            UPSTREAM_ERROR_COOLDOWN_SECONDS,
            f"{type(exc).__name__}: {exc}",
        )

    async def _invoke_provider(
        self,
        config: DecisionProviderConfig,
        evidence: dict[str, Any] | str,
        questions: Sequence[DecisionQuestion],
    ) -> dict[str, NormalizedAnswer]:
        """Отправляет HTTP-запрос в диалекте провайдера и нормализует ответы по именам вопросов."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key.strip():
            headers["Authorization"] = f"Bearer {config.api_key.strip()}"

        request_body = self._build_request_payload(config, evidence, questions)
        timeout_sec = max(0.05, float(config.timeout_ms) / MS_PER_SECOND)

        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout_sec,
        ) as client:
            response = await client.post(
                config.base_url,
                headers=headers,
                json=request_body,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return self._parse_response_payload(config.dialect, data, questions)

    @staticmethod
    def _build_request_payload(
        config: DecisionProviderConfig,
        evidence: dict[str, Any] | str,
        questions: Sequence[DecisionQuestion],
    ) -> dict[str, Any]:
        """Сериализует контекст и микро-вопросы в целевой диалект (`OPENAI_DECISIONS`, `JEV_CLEF`, `OPENAI_CHAT_SCHEMA`)."""
        evidence_str = (
            evidence
            if isinstance(evidence, str)
            else json.dumps(evidence, ensure_ascii=False)
        )

        if config.dialect in (
            DecisionProviderDialect.OPENAI_DECISIONS,
            DecisionProviderDialect.JEV_CLEF,
        ):
            q_specs: dict[str, dict[str, Any]] = {}
            for q in questions:
                spec: dict[str, Any] = {
                    "type": q.question_type.value,
                    "instructions": q.instructions,
                }
                if q.question_type == DecisionQuestionType.CHOICE:
                    spec["choices"] = list(q.choices)
                elif q.question_type == DecisionQuestionType.SCORE:
                    spec["levels"] = list(q.levels)
                q_specs[q.name] = spec

            return {
                "model": config.model,
                "context": evidence_str,
                "questions": q_specs,
            }

        props: dict[str, Any] = {}
        prompt_lines: list[str] = [
            "Evaluate the following evidence and answer every structured micro-question in strict JSON:",
            f"Evidence: {evidence_str}",
            "Questions:",
        ]
        for q in questions:
            prompt_lines.append(f"- {q.name} ({q.question_type.value}): {q.instructions}")
            if q.question_type == DecisionQuestionType.PREDICATE:
                props[q.name] = {"type": "boolean"}
            elif q.question_type == DecisionQuestionType.CHOICE:
                props[q.name] = {"type": "string", "enum": list(q.choices)}
            else:
                props[q.name] = {"type": "number"}

        return {
            "model": config.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": "You are a deterministic structured decision engine."},
                {"role": "user", "content": "\n".join(prompt_lines)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision_batch",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": props,
                        "required": [q.name for q in questions],
                        "additionalProperties": False,
                    },
                },
            },
        }

    @classmethod
    def _parse_response_payload(
        cls,
        dialect: DecisionProviderDialect,
        data: dict[str, Any],
        questions: Sequence[DecisionQuestion],
    ) -> dict[str, NormalizedAnswer]:
        """Разбирает ответ провайдера в канонический словарь `dict[str, NormalizedAnswer]`."""
        raw_answers: dict[str, Any] = {}

        if dialect == DecisionProviderDialect.OPENAI_CHAT_SCHEMA:
            choices_list = data.get("choices")
            if isinstance(choices_list, list) and choices_list:
                first_msg = choices_list[0].get("message", {})
                content_str = first_msg.get("content", "{}")
                if isinstance(content_str, str):
                    raw_answers = json.loads(content_str)
                elif isinstance(content_str, dict):
                    raw_answers = content_str
            elif isinstance(data.get("answers"), dict):
                raw_answers = data["answers"]
        else:
            if isinstance(data.get("answers"), dict):
                raw_answers = data["answers"]
            elif isinstance(data.get("decisions"), dict):
                raw_answers = data["decisions"]
            else:
                raw_answers = data

        normalized: dict[str, NormalizedAnswer] = {}
        for q in questions:
            item = raw_answers.get(q.name)
            normalized[q.name] = cls._normalize_single_answer(q, item)
        return normalized

    @staticmethod
    def _normalize_single_answer(
        question: DecisionQuestion,
        raw: Any,
    ) -> NormalizedAnswer:
        """Нормализует сырой ответ по одному микро-вопросу с проверкой порогов и распределений."""
        q_type = question.question_type
        if isinstance(raw, dict):
            val = raw.get("value")
            prob = float(raw.get("probability", raw.get("confidence", 1.0)))
            conf = float(raw.get("confidence", prob))
            dist_raw = raw.get("distribution", {})
            dist = (
                {str(k): float(v) for k, v in dist_raw.items()}
                if isinstance(dist_raw, dict)
                else {}
            )
        else:
            val = raw
            prob = 1.0
            conf = 1.0
            dist = {}

        prob = max(0.0, min(1.0, prob))
        conf = max(0.0, min(1.0, conf))

        if q_type == DecisionQuestionType.PREDICATE:
            if isinstance(val, bool):
                bool_val = (
                    val and prob >= question.predicate_threshold
                    if "probability" in (raw if isinstance(raw, dict) else {})
                    else val
                )
            elif isinstance(val, (int, float)):
                prob = max(0.0, min(1.0, float(val)))
                bool_val = prob >= question.predicate_threshold
            else:
                bool_val = str(val).strip().lower() in ("true", "1", "yes")
            return NormalizedAnswer(
                name=question.name,
                question_type=q_type,
                value=bool_val,
                probability=prob,
                confidence=conf,
                distribution=dist,
            )

        if q_type == DecisionQuestionType.CHOICE:
            default_choice = question.choices[0] if question.choices else ""
            choice_val = str(val) if val is not None else default_choice
            if question.choices and choice_val not in question.choices:
                choice_val = default_choice
            return NormalizedAnswer(
                name=question.name,
                question_type=q_type,
                value=choice_val,
                probability=prob,
                confidence=conf,
                distribution=dist,
            )

        score_val = float(val) if isinstance(val, (int, float)) else 0.0
        score_val = max(0.0, min(1.0, score_val))
        return NormalizedAnswer(
            name=question.name,
            question_type=q_type,
            value=score_val,
            probability=prob,
            confidence=conf,
            distribution=dist,
        )


__all__ = [
    "AUTH_ERROR_COOLDOWN_SECONDS",
    "DEFAULT_OPENAI_DECISIONS_MODEL",
    "DEFAULT_OPENAI_DECISIONS_NAME",
    "DEFAULT_OPENAI_DECISIONS_URL",
    "DEFAULT_PRIMARY_PRIORITY",
    "QUOTA_COOLDOWN_SECONDS",
    "RATE_LIMIT_COOLDOWN_SECONDS",
    "UPSTREAM_ERROR_COOLDOWN_SECONDS",
    "RuntimeProviderPool",
]
