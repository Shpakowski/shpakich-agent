/**
 * Клиентский модуль управления интерактивным графом Cytoscape.js (Layer 2 Core UI).
 *
 * Отвечает за инициализацию графового холста в контейнере `#cy`, применение кастомной тёмной
 * дизайн-системы, вычисление топологических раскладок (fCoSE, Dagre, Concentric), фильтрацию
 * видимости по типам и статусам компонентов, а также семантическую подсветку и зумирование.
 */

// Идентификаторы поддерживаемых алгоритмов пространственной раскладки графа
const LAYOUT_NAMES = Object.freeze({
  FCOSE: "fcose",
  COSE: "cose",
  DAGRE: "dagre",
  CONCENTRIC: "concentric",
});

// Идентификаторы категорий узлов графа знаний и каталога Harness²
const NODE_KINDS = Object.freeze({
  PROJECT: "project",
  STACK_TAG: "stack_tag",
  GROUP_CONTAINER: "group_container",
  HARNESS_COMPONENT: "harness_component",
  MEMORY: "memory",
  COMPACTION: "compaction",
  CONTEXT_NODE: "context_node",
});

// Жизненные статусы компонентов Harness² для цветовой индикации
const COMPONENT_STATUSES = Object.freeze({
  COMPOSED: "composed",
  PROBING: "probing",
  CANDIDATE: "candidate",
  ACTIVE: "active",
  SYNCED: "synced",
  ARCHIVED: "archived",
  DISABLED: "disabled",
});

// Типы связей между сущностями в графовой топологии
const EDGE_KINDS = Object.freeze({
  SEMANTIC_SIMILARITY: "semantic_similarity",
  CONTEXT_RELATION: "context_relation",
  HAS_STACK_TAG: "has_stack_tag",
  COMPILED_INTO: "compiled_into",
  ORIGINATED_FROM: "originated_from",
  BELONGS_TO_GROUP: "belongs_to_group",
});

// Селекторные CSS-классы Cytoscape для подсветки изменений и поиска
const CANVAS_CLASSES = Object.freeze({
  IS_NEW: "is-new",
  IS_UPDATED: "is-updated",
  DIFF_ADDED: "diff-added",
  SEARCH_HIT: "search-hit",
  DIMMED: "dimmed",
});

// Палитра дизайн-системы Visualizer Dark Theme
const COLOR_TOKENS = Object.freeze({
  BG_CONTAINER: "#111827",
  BORDER_CONTAINER: "#334155",
  TEXT_PRIMARY: "#e5e7eb",
  TEXT_MUTED: "#94a3b8",
  TEXT_BRIGHT: "#f9fafb",
  COLOR_PROJECT: "#38bdf8",
  COLOR_STACK_TAG: "#64748b",
  COLOR_COMPOSED: "#10b981",
  COLOR_PROBING: "#f59e0b",
  COLOR_CANDIDATE: "#06b6d4",
  COLOR_DISABLED: "#f43f5e",
  COLOR_MEMORY: "#ec4899",
  COLOR_COMPACTION: "#8b5cf6",
  COLOR_CONTEXT: "#3b82f6",
  COLOR_SEMANTIC_EDGE: "#a855f7",
  COLOR_CONTEXT_EDGE: "#3b82f6",
  COLOR_DEFAULT_EDGE: "#475569",
  COLOR_SEARCH_HIT: "#38bdf8",
  SELECTION_BORDER: "#ffffff",
  DIMMED_OPACITY: 0.18,
});

// Временные параметры анимаций, масштабирования и отступов
const CANVAS_TIMINGS = Object.freeze({
  LAYOUT_ANIMATION_MS: 500,
  FOCUS_ANIMATION_MS: 400,
  ZOOM_FACTOR: 1.25,
  FIT_PADDING: 32,
  CONCENTRIC_LEVEL_WIDTH: 2,
});

/**
 * Безопасно регистрирует подключаемые расширения Cytoscape.js (Dagre и fCoSE)
 * в глобальном реестре фабрики при их наличии в объекте `window`.
 */
const registerCytoscapeExtensions = () => {
  if (typeof window === "undefined" || typeof window.cytoscape === "undefined") {
    return;
  }
  const cyFactory = window.cytoscape;

  if (typeof window.cytoscapeDagre !== "undefined") {
    try {
      cyFactory.use(window.cytoscapeDagre);
    } catch (_) {
      // Игнорируем ошибку при повторном вызове .use() в одном окне
    }
  }

  if (typeof window.cytoscapeFcose !== "undefined") {
    try {
      cyFactory.use(window.cytoscapeFcose);
    } catch (_) {
      // Игнорируем ошибку при повторном вызове .use() в одном окне
    }
  }
};

/**
 * Собирает декларативную таблицу стилей Cytoscape.js для тёмной темы.
 *
 * Зачем:
 * - Обеспечивает единое визуальное кодирование типов узлов, статусов, рёбер схожести
 *   и интерактивных состояний фокуса/фильтрации без прямого манипулирования DOM.
 *
 * @returns {Array<Object>} Массив правил стилизации селекторов Cytoscape.
 */
const buildCytoscapeStyles = () => {
  return [
    // Базовый стиль всех узлов
    {
      selector: "node",
      style: {
        "label": "data(label)",
        "color": COLOR_TOKENS.TEXT_PRIMARY,
        "font-size": "11px",
        "font-family": "ui-sans-serif, system-ui, -apple-system, sans-serif",
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 6,
        "text-max-width": "155px",
        "text-wrap": "wrap",
        "text-background-color": "#0b0f19",
        "text-background-opacity": 0.88,
        "text-background-padding": "3px",
        "text-background-shape": "roundrectangle",
        "background-color": COLOR_TOKENS.COLOR_STACK_TAG,
        "border-width": 2,
        "border-color": "#1e293b",
        "width": 36,
        "height": 36,
        "transition-property": "background-color, border-color, border-width, opacity",
        "transition-duration": "0.2s",
      },
    },

    // Составные контейнеры группировки компонентов (Compound Nodes)
    {
      selector: `node[kind = "${NODE_KINDS.GROUP_CONTAINER}"]`,
      style: {
        "background-color": COLOR_TOKENS.BG_CONTAINER,
        "background-opacity": 0.72,
        "border-width": 1.5,
        "border-style": "dashed",
        "border-color": COLOR_TOKENS.BORDER_CONTAINER,
        "shape": "round-rectangle",
        "padding": 26,
        "label": "data(label)",
        "color": "#cbd5e1",
        "text-valign": "top",
        "text-halign": "center",
        "text-margin-y": -10,
        "font-size": "12px",
        "font-weight": "bold",
        "text-transform": "uppercase",
        "text-max-width": "260px",
      },
    },

    // Корневые проекты
    {
      selector: `node[kind = "${NODE_KINDS.PROJECT}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_PROJECT,
        "border-color": "#e0f2fe",
        "border-width": 2.5,
        "shape": "hexagon",
        "width": 52,
        "height": 52,
        "font-size": "13px",
        "font-weight": "bold",
      },
    },

    // Теги стека проекта (компактные пилюли с текстом внутри)
    {
      selector: `node[kind = "${NODE_KINDS.STACK_TAG}"]`,
      style: {
        "background-color": "#1e293b",
        "border-color": "#475569",
        "border-width": 1.5,
        "shape": "round-rectangle",
        "width": "label",
        "height": 22,
        "padding": "8px",
        "font-size": "10px",
        "color": "#cbd5e1",
        "text-valign": "center",
        "text-halign": "center",
        "text-margin-y": 0,
        "text-background-opacity": 0,
      },
    },

    // Компоненты Harness² со статусом Composed
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][status = "${COMPONENT_STATUSES.COMPOSED}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_COMPOSED,
        "border-color": "#a7f3d0",
        "shape": "round-rectangle",
      },
    },

    // Компоненты Harness² со статусом Probing
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][status = "${COMPONENT_STATUSES.PROBING}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_PROBING,
        "border-color": "#fde68a",
        "shape": "round-rectangle",
      },
    },

    // Компоненты Harness² со статусом Candidate
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][status = "${COMPONENT_STATUSES.CANDIDATE}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_CANDIDATE,
        "border-color": "#a5f3fc",
        "shape": "round-rectangle",
      },
    },

    // Компоненты Harness² со статусом Disabled
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][status = "${COMPONENT_STATUSES.DISABLED}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_DISABLED,
        "border-color": "#fecdd3",
        "shape": "round-rectangle",
      },
    },

    // Формы по подтипам компонентов Harness²
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][subtype = "skill"]`,
      style: {
        "shape": "round-rectangle",
        "width": 40,
        "height": 34,
      },
    },
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][subtype = "script"]`,
      style: {
        "shape": "hexagon",
        "width": 38,
        "height": 38,
      },
    },
    {
      selector: `node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][subtype = "guardrail"], node[kind = "${NODE_KINDS.HARNESS_COMPONENT}"][subtype = "tool_guidance"]`,
      style: {
        "shape": "octagon",
        "width": 38,
        "height": 38,
      },
    },

    // Извлечённые правила и воспоминания (Memories)
    {
      selector: `node[kind = "${NODE_KINDS.MEMORY}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_MEMORY,
        "border-color": "#fbcfe8",
        "shape": "diamond",
        "width": 36,
        "height": 36,
      },
    },

    // Архивы уплотнённых сессий (Compactions)
    {
      selector: `node[kind = "${NODE_KINDS.COMPACTION}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_COMPACTION,
        "border-color": "#ddd6fe",
        "shape": "barrel",
        "width": 36,
        "height": 36,
      },
    },

    // Сущности графа архитектурного контекста (.context/) — базовый стиль (Module)
    {
      selector: `node[kind = "${NODE_KINDS.CONTEXT_NODE}"]`,
      style: {
        "background-color": COLOR_TOKENS.COLOR_CONTEXT,
        "border-color": "#93c5fd",
        "shape": "round-rectangle",
        "width": 38,
        "height": 32,
      },
    },
    // Точка входа графа .context/ (index.md -> GraphIndex)
    {
      selector: `node[kind = "${NODE_KINDS.CONTEXT_NODE}"][subtype = "GraphIndex"]`,
      style: {
        "background-color": "#f59e0b",
        "border-color": "#fef3c7",
        "border-width": 2.5,
        "shape": "hexagon",
        "width": 46,
        "height": 46,
        "font-weight": "bold",
      },
    },
    // Архитектурные контракты (.context/entities/contracts/)
    {
      selector: `node[kind = "${NODE_KINDS.CONTEXT_NODE}"][subtype = "Contract"]`,
      style: {
        "background-color": "#06b6d4",
        "border-color": "#cffafe",
        "shape": "diamond",
        "width": 38,
        "height": 38,
      },
    },
    // Архитектурные решения ADR (.context/entities/decisions/)
    {
      selector: `node[kind = "${NODE_KINDS.CONTEXT_NODE}"][subtype = "Decision"]`,
      style: {
        "background-color": "#a855f7",
        "border-color": "#f3e8ff",
        "shape": "octagon",
        "width": 36,
        "height": 36,
      },
    },

    // Подсветка новых сущностей, созданных после выбранного окна синхронизации
    {
      selector: `node.${CANVAS_CLASSES.IS_NEW}`,
      style: {
        "border-color": COLOR_TOKENS.COLOR_COMPOSED,
        "border-width": 3,
        "border-style": "solid",
      },
    },

    // Подсветка обновлённых сущностей после синхронизации
    {
      selector: `node.${CANVAS_CLASSES.IS_UPDATED}`,
      style: {
        "border-color": COLOR_TOKENS.COLOR_PROBING,
        "border-width": 3,
        "border-style": "solid",
      },
    },

    // Подсветка изменений относительно сохранённого локального снапшота (Diff View)
    {
      selector: `node.${CANVAS_CLASSES.DIFF_ADDED}`,
      style: {
        "border-color": COLOR_TOKENS.COLOR_CANDIDATE,
        "border-width": 3.5,
        "border-style": "solid",
      },
    },

    // Подсветка совпадений векторного или полнотекстового поиска
    {
      selector: `node.${CANVAS_CLASSES.SEARCH_HIT}`,
      style: {
        "border-color": COLOR_TOKENS.COLOR_SEARCH_HIT,
        "border-width": 3.5,
        "border-style": "solid",
        "opacity": 1.0,
      },
    },

    // Затемнение нерелевантных сущностей при активном поиске
    {
      selector: `node.${CANVAS_CLASSES.DIMMED}`,
      style: {
        "opacity": COLOR_TOKENS.DIMMED_OPACITY,
      },
    },

    // Активный выбранный узел
    {
      selector: "node:selected",
      style: {
        "border-color": COLOR_TOKENS.SELECTION_BORDER,
        "border-width": 3,
      },
    },

    // Базовый стиль рёбер связей
    {
      selector: "edge",
      style: {
        "width": 1.5,
        "line-color": COLOR_TOKENS.COLOR_DEFAULT_EDGE,
        "target-arrow-color": COLOR_TOKENS.COLOR_DEFAULT_EDGE,
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "arrow-scale": 0.8,
        "transition-property": "opacity, line-color, width",
        "transition-duration": "0.2s",
      },
    },

    // Семантические рёбра векторной близости (pgvector Cosine Similarity)
    {
      selector: `edge[kind = "${EDGE_KINDS.SEMANTIC_SIMILARITY}"]`,
      style: {
        "line-color": COLOR_TOKENS.COLOR_SEMANTIC_EDGE,
        "target-arrow-color": COLOR_TOKENS.COLOR_SEMANTIC_EDGE,
        "line-style": "dashed",
        "label": "data(label)",
        "font-size": "9px",
        "color": "#c084fc",
        "text-background-color": "#0b0f19",
        "text-background-opacity": 0.85,
        "text-background-padding": "2px",
        "text-rotation": "autorotate",
        "width": 2,
      },
    },

    // Явные архитектурные зависимости графа .context/
    {
      selector: `edge[kind = "${EDGE_KINDS.CONTEXT_RELATION}"]`,
      style: {
        "line-color": COLOR_TOKENS.COLOR_CONTEXT_EDGE,
        "target-arrow-color": COLOR_TOKENS.COLOR_CONTEXT_EDGE,
        "line-style": "solid",
        "width": 2,
      },
    },

    // Связи компиляции, происхождения и тегирования
    {
      selector: `edge[kind = "${EDGE_KINDS.HAS_STACK_TAG}"], edge[kind = "${EDGE_KINDS.COMPILED_INTO}"], edge[kind = "${EDGE_KINDS.ORIGINATED_FROM}"]`,
      style: {
        "line-color": COLOR_TOKENS.COLOR_DEFAULT_EDGE,
        "target-arrow-color": COLOR_TOKENS.COLOR_DEFAULT_EDGE,
        "arrow-scale": 0.7,
      },
    },

    // Вспомогательные рёбра принадлежности к группе (не отображаются, так как управляются compound-контейнером)
    {
      selector: `edge[kind = "${EDGE_KINDS.BELONGS_TO_GROUP}"]`,
      style: {
        "display": "none",
      },
    },

    // Затемнение рёбер при активном поиске
    {
      selector: `edge.${CANVAS_CLASSES.DIMMED}`,
      style: {
        "opacity": COLOR_TOKENS.DIMMED_OPACITY,
      },
    },
  ];
};

/**
 * Класс управления интерактивным графовым полотном Cytoscape.js.
 */
class CoopGraphCanvas {
  /**
   * Инициализирует холст, регистрирует обработчики кликов и подготавливает контейнер `#cy`.
   *
   * @param {HTMLElement} container DOM-контейнер холста Cytoscape.
   * @param {Object} [options] Опции коллбеков выбора и раскладки.
   * @param {Function} [options.onSelectNode] Вызывается при клике на узел графа.
   * @param {Function} [options.onUnselectNode] Вызывается при клике в пустое место холста.
   * @param {string} [options.defaultLayout] Дефолтный алгоритм раскладки (по умолчанию 'fcose').
   */
  constructor(container, options = {}) {
    this._container = container;
    this._options = options;
    this._onSelectNode = typeof options.onSelectNode === "function" ? options.onSelectNode : () => {};
    this._onUnselectNode = typeof options.onUnselectNode === "function" ? options.onUnselectNode : () => {};
    this._currentLayoutName = options.defaultLayout || LAYOUT_NAMES.FCOSE;
    this._lastNodeCount = 0;
    this._activeKinds = null;
    this._activeStatuses = null;

    registerCytoscapeExtensions();

    this._cy = window.cytoscape({
      container: this._container,
      style: buildCytoscapeStyles(),
      elements: [],
      wheelSensitivity: 0.25,
      minZoom: 0.1,
      maxZoom: 3.5,
    });

    this._bindEvents();
  }

  /**
   * Подключает события интерактивного взаимодействия пользователя с холстом.
   *
   * @private
   */
  _bindEvents() {
    this._cy.on("tap", "node", (evt) => {
      const node = evt.target;
      if (node.data("kind") === NODE_KINDS.GROUP_CONTAINER) {
        return;
      }
      this._onSelectNode(node.data());
    });

    this._cy.on("tap", (evt) => {
      if (evt.target === this._cy) {
        this._onUnselectNode();
      }
    });
  }

  /**
   * Возвращает базовый экземпляр Cytoscape.js для низкоуровневых операций.
   *
   * @returns {Object} Инстанс Cytoscape.
   */
  get cy() {
    return this._cy;
  }

  /**
   * Отрисовывает полный снимок графа с валидацией составных контейнеров и применением фильтров.
   *
   * Зачем:
   * - Защищает от фатальных сбоев Cytoscape при ссылке на несуществующий родительский контейнер (`parent`),
   * - Применяет классы визуальной дифференциации (`is-new`, `is-updated`, `diff-added`),
   * - Сохраняет текущую позицию камеры при неблокирующем периодическом авто-опросе (`preserveViewport`).
   *
   * @param {Object} snapshot DTO снимка графа с бэкенда (`nodes` и `edges`).
   * @param {Object} [renderOptions] Параметры рендера и фильтрации.
   * @param {Set<string>} [renderOptions.activeKinds] Множество активных типов узлов.
   * @param {Set<string>} [renderOptions.activeStatuses] Множество активных статусов компонентов.
   * @param {string} [renderOptions.layoutName] Алгоритм раскладки.
   * @param {Set<string>|Array<string>} [renderOptions.diffNodeIds] Идентификаторы узлов для diff-подсветки.
   * @param {boolean} [renderOptions.preserveViewport] Сохранять ли масштаб и положение камеры.
   */
  renderSnapshot(snapshot, { activeKinds, activeStatuses, layoutName, diffNodeIds, preserveViewport = false } = {}) {
    if (!snapshot || !Array.isArray(snapshot.nodes)) {
      return;
    }

    if (activeKinds) {
      this._activeKinds = activeKinds;
    }
    if (activeStatuses) {
      this._activeStatuses = activeStatuses;
    }

    const targetLayout = layoutName || this._currentLayoutName || LAYOUT_NAMES.FCOSE;
    const diffSet = diffNodeIds ? (diffNodeIds instanceof Set ? diffNodeIds : new Set(diffNodeIds)) : new Set();
    const allNodeIds = new Set(snapshot.nodes.map((n) => n.data.id));

    // Подготовка узлов с валидацией parent-идентификаторов для compound nodes
    const nodeElements = snapshot.nodes.map((n) => {
      const data = { ...n.data };
      if (data.parent && !allNodeIds.has(data.parent)) {
        delete data.parent;
      }
      const classes = [];
      if (data.is_new) {
        classes.push(CANVAS_CLASSES.IS_NEW);
      }
      if (data.is_updated) {
        classes.push(CANVAS_CLASSES.IS_UPDATED);
      }
      if (diffSet.has(data.id)) {
        classes.push(CANVAS_CLASSES.DIFF_ADDED);
      }
      return {
        data,
        classes: classes.join(" "),
      };
    });

    // Валидация рёбер: исключаем висячие связи с несуществующими узлами
    const edgeElements = (snapshot.edges || []).filter((e) => {
      return allNodeIds.has(e.data.source) && allNodeIds.has(e.data.target);
    });

    const shouldPreserveCamera = preserveViewport && this._lastNodeCount === nodeElements.length;
    const savedPan = shouldPreserveCamera ? this._cy.pan() : null;
    const savedZoom = shouldPreserveCamera ? this._cy.zoom() : null;

    this._cy.batch(() => {
      this._cy.elements().remove();
      this._cy.add(nodeElements);
      this._cy.add(edgeElements);
    });

    this.applyFilters(this._activeKinds, this._activeStatuses);
    this._lastNodeCount = nodeElements.length;

    if (shouldPreserveCamera && savedPan && savedZoom) {
      this._cy.viewport({ zoom: savedZoom, pan: savedPan });
    } else {
      this.runLayout(targetLayout);
    }
  }

  /**
   * Запускает пространственный алгоритм раскладки графа с плавной анимацией перемещения.
   *
   * @param {string} layoutName Имя раскладки ('fcose', 'dagre' или 'concentric').
   */
  runLayout(layoutName) {
    this._currentLayoutName = layoutName || this._currentLayoutName;
    const activeElements = this._cy.elements().filter((el) => el.style("display") !== "none");
    const targetEles = activeElements.length > 0 ? activeElements : this._cy.elements();

    if (this._currentLayoutName === LAYOUT_NAMES.FCOSE) {
      try {
        const layout = targetEles.layout({
          name: LAYOUT_NAMES.FCOSE,
          animate: true,
          animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
          fit: true,
          padding: CANVAS_TIMINGS.FIT_PADDING,
          nodeDimensionsIncludeLabels: true,
          uniformNodeDimensions: false,
          packComponents: true,
          randomize: true,
          quality: "default",
          idealEdgeLength: 135,
          nodeRepulsion: 9500,
          nestingFactor: 0.85,
        });
        layout.run();
        return;
      } catch (_) {
        // Fallback на встроенный алгоритм CoSE при недоступности расширения fCoSE
        targetEles.layout({
          name: LAYOUT_NAMES.COSE,
          animate: true,
          animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
          fit: true,
          padding: CANVAS_TIMINGS.FIT_PADDING,
          nodeDimensionsIncludeLabels: true,
        }).run();
        return;
      }
    }

    if (this._currentLayoutName === LAYOUT_NAMES.DAGRE) {
      try {
        const layout = targetEles.layout({
          name: LAYOUT_NAMES.DAGRE,
          rankDir: "LR",
          animate: true,
          animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
          fit: true,
          padding: CANVAS_TIMINGS.FIT_PADDING,
          nodeDimensionsIncludeLabels: true,
          spacingFactor: 1.3,
        });
        layout.run();
        return;
      } catch (_) {
        // Fallback на встроенный CoSE при отсутствии Dagre
        targetEles.layout({
          name: LAYOUT_NAMES.COSE,
          animate: true,
          animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
          fit: true,
          padding: CANVAS_TIMINGS.FIT_PADDING,
        }).run();
        return;
      }
    }

    if (this._currentLayoutName === LAYOUT_NAMES.CONCENTRIC) {
      const layout = targetEles.layout({
        name: LAYOUT_NAMES.CONCENTRIC,
        animate: true,
        animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
        fit: true,
        padding: CANVAS_TIMINGS.FIT_PADDING,
        nodeDimensionsIncludeLabels: true,
        minNodeSpacing: 42,
        concentric: (node) => {
          const kind = node.data("kind");
          if (kind === NODE_KINDS.PROJECT) return 10;
          if (kind === NODE_KINDS.HARNESS_COMPONENT) return 7;
          if (kind === NODE_KINDS.MEMORY || kind === NODE_KINDS.COMPACTION) return 5;
          if (kind === NODE_KINDS.CONTEXT_NODE) return 3;
          return 1;
        },
        levelWidth: () => CANVAS_TIMINGS.CONCENTRIC_LEVEL_WIDTH,
      });
      layout.run();
      return;
    }

    // Дефолтный фолбэк для любого неизвестного имени раскладки
    targetEles.layout({
      name: LAYOUT_NAMES.COSE,
      animate: true,
      animationDuration: CANVAS_TIMINGS.LAYOUT_ANIMATION_MS,
      fit: true,
      padding: CANVAS_TIMINGS.FIT_PADDING,
    }).run();
  }

  /**
   * Динамически скрывает или отображает узлы и рёбра согласно активным чекбоксам фильтров.
   * Составные контейнеры (`group_container`) остаются видимыми, только если содержат хотя бы одного видимого потомка.
   *
   * @param {Set<string>|null} activeKinds Множество разрешённых типов сущностей.
   * @param {Set<string>|null} activeStatuses Множество разрешённых статусов компонентов.
   */
  applyFilters(activeKinds, activeStatuses) {
    this._activeKinds = activeKinds || this._activeKinds;
    this._activeStatuses = activeStatuses || this._activeStatuses;

    this._cy.batch(() => {
      // 1. Фильтрация обычных узлов по типу и статусу
      const regularNodes = this._cy.nodes(`[kind != "${NODE_KINDS.GROUP_CONTAINER}"]`);
      regularNodes.forEach((node) => {
        const kind = node.data("kind");
        const status = node.data("status");
        const matchesKind = !this._activeKinds || this._activeKinds.has(kind);
        const matchesStatus = !this._activeStatuses || !status || this._activeStatuses.has(status);

        if (matchesKind && matchesStatus) {
          node.show();
        } else {
          node.hide();
        }
      });

      // 2. Управление видимостью compound-контейнеров по style("display") дочерних элементов
      const groupNodes = this._cy.nodes(`[kind = "${NODE_KINDS.GROUP_CONTAINER}"]`);
      groupNodes.forEach((group) => {
        const hasDisplayedChildren = group
          .children()
          .some((child) => child.style("display") !== "none");
        if (hasDisplayedChildren) {
          group.show();
        } else {
          group.hide();
        }
      });

      // 3. Синхронизация видимости связей по style("display") концов ребра
      this._cy.edges().forEach((edge) => {
        const srcDisplayed = edge.source().style("display") !== "none";
        const tgtDisplayed = edge.target().style("display") !== "none";
        if (srcDisplayed && tgtDisplayed) {
          if (edge.data("kind") !== EDGE_KINDS.BELONGS_TO_GROUP) {
            edge.show();
          }
        } else {
          edge.hide();
        }
      });
    });
  }

  /**
   * Подсвечивает найденные узлы и затемняет нерелевантные сущности при поиске.
   *
   * @param {Array<string>|Set<string>|null} hitNodeIds Идентификаторы найденных узлов.
   */
  highlightSearchHits(hitNodeIds) {
    if (!hitNodeIds || (Array.isArray(hitNodeIds) && hitNodeIds.length === 0)) {
      this._cy.elements().removeClass(`${CANVAS_CLASSES.SEARCH_HIT} ${CANVAS_CLASSES.DIMMED}`);
      return;
    }

    const hitSet = hitNodeIds instanceof Set ? hitNodeIds : new Set(hitNodeIds);

    this._cy.batch(() => {
      this._cy.nodes().forEach((node) => {
        if (node.data("kind") === NODE_KINDS.GROUP_CONTAINER) {
          node.removeClass(CANVAS_CLASSES.DIMMED);
          return;
        }

        if (hitSet.has(node.id())) {
          node.addClass(CANVAS_CLASSES.SEARCH_HIT);
          node.removeClass(CANVAS_CLASSES.DIMMED);
        } else {
          node.removeClass(CANVAS_CLASSES.SEARCH_HIT);
          node.addClass(CANVAS_CLASSES.DIMMED);
        }
      });

      this._cy.edges().forEach((edge) => {
        if (hitSet.has(edge.source().id()) && hitSet.has(edge.target().id())) {
          edge.removeClass(CANVAS_CLASSES.DIMMED);
        } else {
          edge.addClass(CANVAS_CLASSES.DIMMED);
        }
      });
    });
  }

  /**
   * Выбирает указанный узел, центрирует на нём камеру с плавным зумом и открывает инспектор.
   *
   * @param {string} nodeId Идентификатор узла для фокусировки.
   */
  focusNode(nodeId) {
    const target = this._cy.getElementById(nodeId);
    if (!target || target.length === 0) {
      return;
    }

    if (!target.visible()) {
      target.show();
      const parent = target.parent();
      if (parent && parent.length > 0) {
        parent.show();
      }
    }

    this._cy.elements().unselect();
    target.select();

    this._cy.animate(
      {
        center: { eles: target },
        zoom: Math.max(this._cy.zoom(), 1.2),
      },
      {
        duration: CANVAS_TIMINGS.FOCUS_ANIMATION_MS,
      }
    );

    this._onSelectNode(target.data());
  }

  /**
   * Плавно увеличивает масштаб графа относительно центра экрана.
   */
  zoomIn() {
    this._cy.zoom({
      level: this._cy.zoom() * CANVAS_TIMINGS.ZOOM_FACTOR,
      renderedPosition: {
        x: this._cy.width() / 2,
        y: this._cy.height() / 2,
      },
    });
  }

  /**
   * Плавно уменьшает масштаб графа относительно центра экрана.
   */
  zoomOut() {
    this._cy.zoom({
      level: this._cy.zoom() / CANVAS_TIMINGS.ZOOM_FACTOR,
      renderedPosition: {
        x: this._cy.width() / 2,
        y: this._cy.height() / 2,
      },
    });
  }

  /**
   * Центрирует и масштабирует всю видимую топологию графа в границы полотна.
   */
  fit() {
    this._cy.fit(undefined, CANVAS_TIMINGS.FIT_PADDING);
  }
}

// Экспорт класса холста в глобальную область видимости браузера
if (typeof window !== "undefined") {
  window.CoopGraphCanvas = CoopGraphCanvas;
}
