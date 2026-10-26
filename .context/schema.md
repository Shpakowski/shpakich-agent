---
type: GraphSchema
version: "2026.2"
project: mcp-coop-agentic-factory
updated_at: "2026-10-24"
---

# Онтология и Правила Валидации Контекстного Графа (`.context/schema.md`)

> **Назначение:** Формальный контракт локального графа знаний проекта (`Graph-as-Code`), определяющий структуру узлов, типы направленных связей и инварианты целостности, проверяемые движком `ContextGraphManager` (`src/core/graph/context_graph.py`) на базе `NetworkX DiGraph` и `Pydantic v2`.

---

## 1. Архитектурная модель (`Graph-as-Code`)

Контекстный граф хранится непосредственно в Git-репозитории проекта в директории `.context/` в виде человекочитаемых Markdown-документов с типизированной YAML-шапкой (Frontmatter) и двусторонними связями `[[wiki-links]]`.

При чтении и валидации ядро `mcp-coop-agentic-factory-core` собирает из файлов `.context/entities/**/*.md` ориентированный граф **`networkx.DiGraph`**, где:
* **Вершины (`Nodes`)** — архитектурные сущности проекта (`Module`, `Contract`, `Decision`), индексируемые по уникальному строковому идентификатору `id`.
* **Рёбра (`Edges`)** — типизированные направленные зависимости между сущностями (`depends_on`, `implements`, `governed_by`, `references`).

---

## 2. Классы узлов и файловая топология

Все узлы графа располагаются строго внутри `.context/entities/` и делятся на 3 канонических класса (`ContextEntityClass`):

| Класс (`class`) | Каталог хранения | Шаблон имени файла и `id` | Семантика узла |
| :--- | :--- | :--- | :--- |
| **`Module`** | `.context/entities/modules/` | `mod-<slug>.md` | Логически завершённый модуль кода или инфраструктурный контур, объединяющий физические файлы репозитория. |
| **`Contract`** | `.context/entities/contracts/` | `contract-<slug>.md` | Межмодульный или межслойный интерфейс: схема БД, протокол хуков, REST/MCP API или формат обмена сообщениями. |
| **`Decision`** | `.context/entities/decisions/` | `adr-<slug>.md` | Архитектурное решение (Architecture Decision Record), фиксирующее выбранный паттерн, его альтернативы и компромиссы. |

---

## 3. Схема YAML Frontmatter узла (`ContextNodeFrontmatter`)

Каждый файл в `.context/entities/**/*.md` обязан начинаться с валидного блока YAML Frontmatter, ограниченного маркерами `---`:

```yaml
---
type: Entity
class: Module                # Module | Contract | Decision
id: mod-python-core          # Уникальный идентификатор узла (совпадает с именем файла без .md)
name: Базовый каркас Python-ядра и слой хранения
status: Active               # Active | Draft | Deprecated
stack: [python, fastapi, postgresql, redis, networkx]
files:
  - pyproject.toml
  - src/core/main.py
depends_on:
  - "[[mod-context-hooks]]"
implements:
  - "[[contract-mcp-tools]]"
governed_by:
  - "[[adr-zero-hardcode]]"
---
```

### Спецификация полей шапки:
1. **`type`** (`str`, default: `"Entity"`): Маркер сущности графа (в отличие от `GraphIndex` в `index.md` и `GraphSchema` в `schema.md`).
2. **`class`** (`ContextEntityClass`, обязательное): Класс вершины (`Module`, `Contract`, `Decision`).
3. **`id`** (`str`, обязательное): Уникальный первичный ключ вершины в `nx.DiGraph`.
4. **`name`** (`str`, обязательное): Краткое предметное название узла.
5. **`status`** (`ContextEntityStatus`, default: `"Active"`): Жизненный цикл узла (`Active`, `Draft`, `Deprecated`).
6. **`stack`** (`list[str]`, default: `[]`): Технологические теги для фильтрации релевантных правил и памяти в цикле `Harness`.
7. **`files`** (`list[str]`, default: `[]`): Список относительных путей к реальным файлам репозитория (от корня рабочей директории), за которые отвечает данный узел.
8. **`depends_on`**, **`implements`**, **`governed_by`** (`list[str]`, default: `[]`): Списки исходящих связей в формате `[[target-id]]` или `target-id`.

---

## 4. Типы направленных рёбер (`ContextRelationType`)

Движок графа формирует направленные рёбра `source -> target` с атрибутом `relation` четырёх типов:

1. **`depends_on`** (из поля `depends_on` в YAML): Узел `source` функционально или инфраструктурно зависит от узла `target`.
2. **`implements`** (из поля `implements` в YAML): Модуль `source` реализует спецификацию или интерфейс контракта `target`.
3. **`governed_by`** (из поля `governed_by` в YAML): Узел `source` подчиняется архитектурному ограничению или решению `target` (ADR).
4. **`references`** (из тела Markdown `body_md`): Любая inline wiki-ссылка вида `[[target-id]]` или `[[target-id|текст]]` внутри описания узла автоматически создаёт контекстное ребро `references` (если между этими узлами ещё не задана более строгая связь из YAML-шапки и `target != source`).

---

## 5. Правила целостности и валидации (`build_and_validate`)

Метод `ContextGraphManager.build_and_validate(workspace_path)` проверяет граф на соблюдение трёх жёстких инвариантов (`GraphValidationReport.is_valid == True`):

1. **Правило отсутствия фантомных файлов (`No Phantom Nodes / No Missing Files`):**
   - Каждый путь, перечисленный в списке `files:` любого узла, обязан физически существовать на диске относительно корня проекта (`workspace_root / rel_path`).
   - Запрещено добавлять в `files:` ещё не созданные («будущие») файлы. При обнаружении отсутствующего файла узел попадает в словарь ошибок `missing_files[node_id]`.
2. **Правило отсутствия битых ссылок (`No Broken Links`):**
   - Каждая wiki-ссылка `[[target-id]]` в полях `depends_on`, `implements`, `governed_by`, а также в теле Markdown-документа (`body_md`), обязана указывать на реально существующий `id` узла в `.context/entities/`.
   - Если целевой узел не найден, ссылка фиксируется в словаре `broken_links[node_id]`, а валидация возвращает `is_valid = False`.
3. **Валидность синтаксиса и схемы (`Zero Parse Errors`):**
   - Все `.md` файлы внутри `.context/entities/` обязаны содержать корректный YAML Frontmatter и проходить строгую проверку типов `ContextNodeFrontmatter` (`Pydantic v2`). Любое нарушение фиксируется в `parse_errors`.

---

## 6. Структура тела узла и секция `AI Traces`

Тело каждого Markdown-узла после YAML-шапки должно содержать:
1. Заголовок первого уровня `# Название узла (<id>)` и краткое описание его архитектурной роли.
2. Структурированный разбор ключевых файлов, классов и потоков данных внутри узла.
3. Обязательную финальную секцию **`## Логи контекста (AI Traces)`** с хронологическим журналом изменений в формате:
   ```markdown
   ## Логи контекста (AI Traces)
   - YYYY-MM-DD: Краткое инженерное резюме выполненной итерации и затронутых контрактов.
   ```
