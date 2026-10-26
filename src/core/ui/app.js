/**
 * Главный клиентский контроллер приложения интерактивного визуализатора (Layer 2 Core UI).
 *
 * Отвечает за координацию UI дашборда: загрузку снимка графа с бэкенда (`/api/visualizer/graph`),
 * дифференциацию снимков через `localStorage`, фоновый Live Auto-Poll, дебаунсинг гибридного
 * RAG-поиска (`pgvector + fts + trgm`), обновление счетчиков KPI и управление правым инспектором
 * сущностей с возможностью мутации статусов компонентов каталога Harness.
 */

// Селекторы DOM-элементов дашборда для исключения жестко закодированных строковых литералов
const DOM_SELECTORS = Object.freeze({
  CY_CONTAINER: "#cy",
  WORKSPACE_INPUT: "#workspace-input",
  SEARCH_INPUT: "#search-input",
  SEARCH_CLEAR_BTN: "#search-clear-btn",
  LAYOUT_SELECT: "#layout-select",
  SINCE_SELECT: "#since-select",
  SEMANTIC_TOGGLE: "#semantic-toggle",
  SIMILARITY_RANGE: "#similarity-range",
  SIMILARITY_VAL: "#similarity-val",
  LIVE_POLL_TOGGLE: "#live-poll-toggle",
  DIFF_LAST_VIEW_BTN: "#diff-last-view-btn",
  CONSOLIDATE_BTN: "#consolidate-btn",
  REFRESH_BTN: "#refresh-btn",
  DB_STATUS_BADGE: "#db-status-badge",
  DB_STATUS_TEXT: "#db-status-badge .status-text",
  ZOOM_IN_BTN: "#zoom-in-btn",
  ZOOM_OUT_BTN: "#zoom-out-btn",
  FIT_GRAPH_BTN: "#fit-graph-btn",
  CANVAS_TOAST: "#canvas-toast",
  KIND_FILTERS: "#kind-filters",
  STATUS_FILTERS: "#status-filters",
  SEARCH_META: "#search-meta",
  SEARCH_RESULTS_LIST: "#search-results-list",
  RECENT_CHANGES_COUNT: "#recent-changes-count",
  RECENT_CHANGES_LIST: "#recent-changes-list",
  INSPECTOR_EMPTY: "#inspector-empty",
  INSPECTOR_CONTENT: "#inspector-content",
  INSP_TITLE: "#insp-title",
  INSP_ID: "#insp-id",
  INSP_BADGES: "#insp-badges",
  INSP_ACTIONS: "#insp-actions",
  INSP_ACTIONS_SECTION: "#insp-actions",
  INSP_TAGS: "#insp-tags",
  INSP_METRICS: "#insp-metrics",
  INSP_BODY: "#insp-body",
  STAT_PROJECTS: "#stat-projects",
  STAT_COMPONENTS: "#stat-components",
  STAT_SKILLS: "#stat-skills",
  STAT_SCRIPTS: "#stat-scripts",
  STAT_GUARDRAILS: "#stat-guardrails",
  STAT_MEMORIES: "#stat-memories",
  STAT_COMPACTIONS: "#stat-compactions",
  STAT_CONTEXT: "#stat-context",
  STAT_EMBEDDED: "#stat-embedded",
  STAT_NEW_SYNC: "#stat-new-sync",
  STAT_UPDATED_SYNC: "#stat-updated-sync",
  STAT_HOOKS_PROVIDERS: "#stat-hooks-providers",
});

// REST API эндпоинты ядра FastAPI
const API_ENDPOINTS = Object.freeze({
  GRAPH_SNAPSHOT: "/api/visualizer/graph",
  KNOWLEDGE_SEARCH: "/api/visualizer/search",
  COMPONENT_STATUS: (slug) => `/api/harness/components/${encodeURIComponent(slug)}`,
  CONSOLIDATE: "/api/harness/consolidate",
});

// Ключи локального хранилища браузера
const STORAGE_KEYS = Object.freeze({
  LAST_SNAPSHOT: "coop_visualizer_last_snapshot_v1",
});

// Константы задержек, интервалов таймеров и лимитов поиска
const TIMING_CONSTANTS = Object.freeze({
  LIVE_POLL_INTERVAL_MS: 5000,
  SEARCH_DEBOUNCE_MS: 300,
  TOAST_DURATION_MS: 3200,
  DEFAULT_SEARCH_TOP_K: 15,
});

// Текстовые статусы подключения к базе данных ядра
const STATUS_PILL_TEXTS = Object.freeze({
  CONNECTED: "Connected (PostgreSQL 18)",
  OFFLINE: "Offline Fallback (.context + Seed)",
});

// Имя типа сущности компонента Harness для отображения панели мутации статуса
const HARNESS_COMPONENT_KIND = "harness_component";

/**
 * Экранирует спецсимволы HTML для защиты от XSS при динамической вставке в разметку.
 *
 * @param {string} text Исходная строка.
 * @returns {string} Безопасная экранированная строка.
 */
const escapeHtml = (text) => {
  if (typeof text !== "string") {
    return text === null || text === undefined ? "" : String(text);
  }
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

/**
 * Форматирует ISO временную метку в короткое время дашборда (ЧЧ:ММ:СС).
 *
 * @param {string|null} isoString Строка времени в формате ISO.
 * @returns {string} Форматированное локальное время.
 */
const formatTime = (isoString) => {
  if (!isoString) {
    return "";
  }
  try {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
      return isoString;
    }
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch (_) {
    return isoString;
  }
};

/**
 * Главный контроллер приложения визуализатора.
 */
class VisualizerApp {
  constructor() {
    this._canvas = null;
    this._currentSnapshot = null;
    this._activeDiffIds = null;
    this._livePollTimer = null;
    this._searchDebounceTimer = null;
    this._toastTimer = null;
    this._isFetchInFlight = false;
    this._selectedNodeData = null;
  }

  /**
   * Инициализирует приложение, подключает холст Cytoscape и слушает события элементов интерфейса.
   */
  init() {
    this._initElements();
    this._readQueryParams();
    this._initCanvas();
    this._bindEvents();
    this.fetchSnapshot({ preserveViewport: false });
  }

  /**
   * Кэширует ссылки на ключевые элементы DOM дерева.
   *
   * @private
   */
  _initElements() {
    this._el = {
      workspaceInput: document.querySelector(DOM_SELECTORS.WORKSPACE_INPUT),
      searchInput: document.querySelector(DOM_SELECTORS.SEARCH_INPUT),
      searchClearBtn: document.querySelector(DOM_SELECTORS.SEARCH_CLEAR_BTN),
      layoutSelect: document.querySelector(DOM_SELECTORS.LAYOUT_SELECT),
      sinceSelect: document.querySelector(DOM_SELECTORS.SINCE_SELECT),
      semanticToggle: document.querySelector(DOM_SELECTORS.SEMANTIC_TOGGLE),
      similarityRange: document.querySelector(DOM_SELECTORS.SIMILARITY_RANGE),
      similarityVal: document.querySelector(DOM_SELECTORS.SIMILARITY_VAL),
      livePollToggle: document.querySelector(DOM_SELECTORS.LIVE_POLL_TOGGLE),
      diffLastViewBtn: document.querySelector(DOM_SELECTORS.DIFF_LAST_VIEW_BTN),
      consolidateBtn: document.querySelector(DOM_SELECTORS.CONSOLIDATE_BTN),
      refreshBtn: document.querySelector(DOM_SELECTORS.REFRESH_BTN),
      dbStatusBadge: document.querySelector(DOM_SELECTORS.DB_STATUS_BADGE),
      dbStatusText: document.querySelector(DOM_SELECTORS.DB_STATUS_TEXT),
      zoomInBtn: document.querySelector(DOM_SELECTORS.ZOOM_IN_BTN),
      zoomOutBtn: document.querySelector(DOM_SELECTORS.ZOOM_OUT_BTN),
      fitGraphBtn: document.querySelector(DOM_SELECTORS.FIT_GRAPH_BTN),
      canvasToast: document.querySelector(DOM_SELECTORS.CANVAS_TOAST),
      kindFilters: document.querySelector(DOM_SELECTORS.KIND_FILTERS),
      statusFilters: document.querySelector(DOM_SELECTORS.STATUS_FILTERS),
      searchMeta: document.querySelector(DOM_SELECTORS.SEARCH_META),
      searchResultsList: document.querySelector(DOM_SELECTORS.SEARCH_RESULTS_LIST),
      recentChangesCount: document.querySelector(DOM_SELECTORS.RECENT_CHANGES_COUNT),
      recentChangesList: document.querySelector(DOM_SELECTORS.RECENT_CHANGES_LIST),
      inspectorEmpty: document.querySelector(DOM_SELECTORS.INSPECTOR_EMPTY),
      inspectorContent: document.querySelector(DOM_SELECTORS.INSPECTOR_CONTENT),
      inspTitle: document.querySelector(DOM_SELECTORS.INSP_TITLE),
      inspId: document.querySelector(DOM_SELECTORS.INSP_ID),
      inspBadges: document.querySelector(DOM_SELECTORS.INSP_BADGES),
      inspActions: document.querySelector(DOM_SELECTORS.INSP_ACTIONS),
      inspTags: document.querySelector(DOM_SELECTORS.INSP_TAGS),
      inspMetrics: document.querySelector(DOM_SELECTORS.INSP_METRICS),
      inspBody: document.querySelector(DOM_SELECTORS.INSP_BODY),
    };
  }

  /**
   * Считывает начальный параметр `?workspace=` из URL браузера в поле ввода пути.
   *
   * @private
   */
  _readQueryParams() {
    if (typeof window === "undefined" || !window.location) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const initialWorkspace = params.get("workspace");
    if (initialWorkspace && this._el.workspaceInput) {
      this._el.workspaceInput.value = initialWorkspace.trim();
    }
  }

  /**
   * Создает экземпляр `CoopGraphCanvas` внутри контейнера `#cy` со связыванием событий выбора узла.
   *
   * @private
   */
  _initCanvas() {
    const cyContainer = document.querySelector(DOM_SELECTORS.CY_CONTAINER);
    if (!cyContainer || typeof window.CoopGraphCanvas === "undefined") {
      console.warn("Cytoscape canvas container or CoopGraphCanvas class is unavailable");
      return;
    }

    this._canvas = new window.CoopGraphCanvas(cyContainer, {
      defaultLayout: this._el.layoutSelect ? this._el.layoutSelect.value : "fcose",
      onSelectNode: (nodeData) => {
        this.openInspector(nodeData);
      },
      onUnselectNode: () => {
        this.closeInspector();
      },
    });
  }

  /**
   * Подключает события интерактивных элементов шапки, фильтров, поиска и инспектора.
   *
   * @private
   */
  _bindEvents() {
    // 1. Управление масштабированием холста
    if (this._el.zoomInBtn) {
      this._el.zoomInBtn.addEventListener("click", () => this._canvas && this._canvas.zoomIn());
    }
    if (this._el.zoomOutBtn) {
      this._el.zoomOutBtn.addEventListener("click", () => this._canvas && this._canvas.zoomOut());
    }
    if (this._el.fitGraphBtn) {
      this._el.fitGraphBtn.addEventListener("click", () => this._canvas && this._canvas.fit());
    }

    // 2. Селекторы раскладки, окна выборки и семантических ребер
    if (this._el.layoutSelect) {
      this._el.layoutSelect.addEventListener("change", () => {
        if (this._canvas) {
          this._canvas.runLayout(this._el.layoutSelect.value);
        }
      });
    }

    if (this._el.sinceSelect) {
      this._el.sinceSelect.addEventListener("change", () => {
        this.fetchSnapshot({ preserveViewport: true });
      });
    }

    if (this._el.semanticToggle) {
      this._el.semanticToggle.addEventListener("change", () => {
        this.fetchSnapshot({ preserveViewport: true });
      });
    }

    if (this._el.similarityRange) {
      this._el.similarityRange.addEventListener("input", () => {
        if (this._el.similarityVal) {
          this._el.similarityVal.textContent = this._el.similarityRange.value;
        }
      });
      this._el.similarityRange.addEventListener("change", () => {
        this.fetchSnapshot({ preserveViewport: true });
      });
    }

    // 3. Кнопка ручного обновления и поле рабочей области
    if (this._el.refreshBtn) {
      this._el.refreshBtn.addEventListener("click", () => {
        this.fetchSnapshot({ preserveViewport: false });
      });
    }

    if (this._el.workspaceInput) {
      this._el.workspaceInput.addEventListener("keydown", (evt) => {
        if (evt.key === "Enter") {
          this.fetchSnapshot({ preserveViewport: false });
        }
      });
      this._el.workspaceInput.addEventListener("change", () => {
        this.fetchSnapshot({ preserveViewport: false });
      });
    }

    // 4. Чекбоксы фильтрации по видам и статусам
    if (this._el.kindFilters) {
      this._el.kindFilters.addEventListener("change", () => {
        if (this._canvas) {
          this._canvas.applyFilters(this.getActiveKinds(), this.getActiveStatuses());
        }
      });
    }

    if (this._el.statusFilters) {
      this._el.statusFilters.addEventListener("change", () => {
        if (this._canvas) {
          this._canvas.applyFilters(this.getActiveKinds(), this.getActiveStatuses());
        }
      });
    }

    // 5. Дифференциация с предыдущим снимком (Diff Last View)
    if (this._el.diffLastViewBtn) {
      this._el.diffLastViewBtn.addEventListener("click", () => {
        this.handleDiffLastView();
      });
    }

    // 6. Неблокирующий Live Auto-Poll с пятисекундным интервалом
    if (this._el.livePollToggle) {
      this._el.livePollToggle.addEventListener("change", () => {
        this.toggleLivePoll(this._el.livePollToggle.checked);
      });
    }

    // 7. Дебаунсированный гибридный поиск по базе знаний
    if (this._el.searchInput) {
      this._el.searchInput.addEventListener("input", () => {
        clearTimeout(this._searchDebounceTimer);
        const query = this._el.searchInput.value.trim();
        if (!query) {
          this.clearSearch();
          return;
        }
        this._searchDebounceTimer = setTimeout(() => {
          this.executeSearch(query);
        }, TIMING_CONSTANTS.SEARCH_DEBOUNCE_MS);
      });
    }

    if (this._el.searchClearBtn) {
      this._el.searchClearBtn.addEventListener("click", () => {
        if (this._el.searchInput) {
          this._el.searchInput.value = "";
        }
        this.clearSearch();
      });
    }

    // 8. Кнопки мутации статуса в инспекторе сущностей
    if (this._el.inspActions) {
      this._el.inspActions.addEventListener("click", (evt) => {
        const btn = evt.target.closest("button[data-set-status]");
        if (btn) {
          const newStatus = btn.dataset.setStatus;
          this.handleStatusUpdate(newStatus, btn);
        }
      });
    }

    // 9. Кнопка консолидации компонентов Harness
    if (this._el.consolidateBtn) {
      this._el.consolidateBtn.addEventListener("click", () => {
        this.handleConsolidate();
      });
    }
  }

  /**
   * Считывает множество активных чекбоксов категорий узлов.
   *
   * @returns {Set<string>} Множество отмеченных типов узлов.
   */
  getActiveKinds() {
    const checked = document.querySelectorAll(`${DOM_SELECTORS.KIND_FILTERS} input[data-kind]:checked`);
    return new Set(Array.from(checked).map((el) => el.dataset.kind));
  }

  /**
   * Считывает множество активных чекбоксов статусов компонентов.
   *
   * @returns {Set<string>} Множество отмеченных статусов.
   */
  getActiveStatuses() {
    const checked = document.querySelectorAll(`${DOM_SELECTORS.STATUS_FILTERS} input[data-status]:checked`);
    return new Set(Array.from(checked).map((el) => el.dataset.status));
  }

  /**
   * Загружает снимок графа с сервера и обновляет дашборд.
   *
   * @param {Object} [options] Опции запроса.
   * @param {boolean} [options.preserveViewport] Сохранять ли положение камеры.
   */
  async fetchSnapshot({ preserveViewport = false } = {}) {
    if (this._isFetchInFlight) {
      return;
    }
    this._isFetchInFlight = true;

    try {
      const workspace = this._el.workspaceInput ? this._el.workspaceInput.value.trim() : "";
      const sinceMinutes = this._el.sinceSelect ? this._el.sinceSelect.value : "60";
      const includeSemantic = this._el.semanticToggle ? this._el.semanticToggle.checked : true;
      const minSimilarity = this._el.similarityRange ? this._el.similarityRange.value : "0.78";

      const queryParams = new URLSearchParams({
        workspace_path: workspace,
        since_minutes: sinceMinutes,
        include_semantic: includeSemantic ? "true" : "false",
        min_similarity: minSimilarity,
      });

      const response = await fetch(`${API_ENDPOINTS.GRAPH_SNAPSHOT}?${queryParams.toString()}`);
      if (!response.ok) {
        throw new Error(`Graph fetch failed with HTTP ${response.status}`);
      }

      const snapshot = await response.json();
      this._currentSnapshot = snapshot;

      this.updateDbStatus(Boolean(snapshot.stats && snapshot.stats.db_connected));
      this.updateStatsStrip(snapshot.stats || {});
      this.updateRecentChanges(snapshot.recent_changes || []);

      if (this._canvas) {
        this._canvas.renderSnapshot(snapshot, {
          activeKinds: this.getActiveKinds(),
          activeStatuses: this.getActiveStatuses(),
          layoutName: this._el.layoutSelect ? this._el.layoutSelect.value : "fcose",
          diffNodeIds: this._activeDiffIds,
          preserveViewport,
        });
      }
    } catch (err) {
      console.error("Failed to load graph snapshot:", err);
      this.showToast(err.message || "Failed to load snapshot", "error");
    } finally {
      this._isFetchInFlight = false;
    }
  }

  /**
   * Обновляет индикатор доступности PostgreSQL и Redis ядра.
   *
   * @param {boolean} isConnected Флаг успешного соединения с СУБД.
   */
  updateDbStatus(isConnected) {
    if (!this._el.dbStatusBadge || !this._el.dbStatusText) {
      return;
    }
    if (isConnected) {
      this._el.dbStatusBadge.classList.remove("status-error");
      this._el.dbStatusText.textContent = STATUS_PILL_TEXTS.CONNECTED;
    } else {
      this._el.dbStatusBadge.classList.add("status-error");
      this._el.dbStatusText.textContent = STATUS_PILL_TEXTS.OFFLINE;
    }
  }

  /**
   * Заполняет плашку KPI-счётчиков актуальными метриками снимка.
   *
   * @param {Object} stats DTO агрегированной статистики ядра.
   */
  updateStatsStrip(stats) {
    const setVal = (selector, val) => {
      const node = document.querySelector(selector);
      if (node) {
        node.textContent = String(val ?? 0);
      }
    };

    setVal(DOM_SELECTORS.STAT_PROJECTS, stats.projects_count);
    setVal(DOM_SELECTORS.STAT_COMPONENTS, stats.harness_components_count);
    setVal(DOM_SELECTORS.STAT_SKILLS, stats.skills_count);
    setVal(DOM_SELECTORS.STAT_SCRIPTS, stats.scripts_count);
    setVal(DOM_SELECTORS.STAT_GUARDRAILS, stats.guardrails_count);
    setVal(DOM_SELECTORS.STAT_MEMORIES, stats.memories_count);
    setVal(DOM_SELECTORS.STAT_COMPACTIONS, stats.compactions_count);
    setVal(DOM_SELECTORS.STAT_CONTEXT, stats.context_nodes_count);
    setVal(DOM_SELECTORS.STAT_EMBEDDED, stats.embedded_nodes_count);
    setVal(DOM_SELECTORS.STAT_NEW_SYNC, stats.new_after_sync_count);
    setVal(DOM_SELECTORS.STAT_UPDATED_SYNC, stats.updated_after_sync_count);

    const hooksNode = document.querySelector(DOM_SELECTORS.STAT_HOOKS_PROVIDERS);
    if (hooksNode) {
      hooksNode.textContent = `${stats.hook_events_count ?? 0} / ${stats.providers_count ?? 0}`;
    }
  }

  /**
   * Формирует список недавних изменений в левой панели.
   *
   * @param {Array<Object>} recentChanges Список изменённых или созданных сущностей.
   */
  updateRecentChanges(recentChanges) {
    if (!this._el.recentChangesList || !this._el.recentChangesCount) {
      return;
    }

    this._el.recentChangesCount.textContent = String(recentChanges.length);
    this._el.recentChangesList.innerHTML = "";

    if (recentChanges.length === 0) {
      this._el.recentChangesList.innerHTML = `
        <div style="font-size:11px; color:var(--text-muted); padding:8px 4px;">
          No changes recorded in the selected time window.
        </div>
      `;
      return;
    }

    recentChanges.forEach((item) => {
      const card = document.createElement("div");
      card.className = "recent-change-card";
      card.dataset.nodeId = item.node_id;

      const isNew = item.change_type === "new" || item.change_type === "created";
      const badgeClass = isNew ? "badge-new" : "badge-upd";
      const timeStr = formatTime(item.timestamp);
      const labelText = escapeHtml(item.label || item.node_id);

      card.innerHTML = `
        <div class="recent-change-meta">
          <span class="badge-tag ${badgeClass}">${escapeHtml(item.change_type)}</span>
          <span class="recent-change-time">${escapeHtml(timeStr)}</span>
        </div>
        <div class="recent-change-label" title="${labelText}">${labelText}</div>
      `;

      card.addEventListener("click", () => {
        if (this._canvas) {
          this._canvas.focusNode(item.node_id);
        }
      });

      this._el.recentChangesList.appendChild(card);
    });
  }

  /**
   * Сравнивает текущий снимок с сохранённым в localStorage слепком узлов и подсвечивает дельту.
   */
  handleDiffLastView() {
    if (!this._currentSnapshot || !Array.isArray(this._currentSnapshot.nodes)) {
      this.showToast("No active snapshot available to diff", "error");
      return;
    }

    const currentMap = {};
    this._currentSnapshot.nodes.forEach((n) => {
      const data = n.data || {};
      currentMap[data.id] = data.updated_at || data.created_at || "baseline";
    });

    let storedSnapshot = null;
    try {
      const storedRaw = localStorage.getItem(STORAGE_KEYS.LAST_SNAPSHOT);
      if (storedRaw) {
        storedSnapshot = JSON.parse(storedRaw);
      }
    } catch (_) {
      // Игнорируем ошибки парсинга при повреждённом хранилище
    }

    const diffNodeIds = new Set();
    let newCount = 0;
    let updatedCount = 0;

    if (storedSnapshot && storedSnapshot.nodes) {
      const prevNodes = storedSnapshot.nodes;
      Object.entries(currentMap).forEach(([nodeId, ts]) => {
        if (!(nodeId in prevNodes)) {
          newCount += 1;
          diffNodeIds.add(nodeId);
        } else if (prevNodes[nodeId] !== ts) {
          updatedCount += 1;
          diffNodeIds.add(nodeId);
        }
      });
      this.showToast(`Diff: ${newCount} added, ${updatedCount} updated since last view`, "success");
    } else {
      this.showToast("Baseline snapshot saved (first view recorded)", "info");
    }

    try {
      localStorage.setItem(
        STORAGE_KEYS.LAST_SNAPSHOT,
        JSON.stringify({
          saved_at: new Date().toISOString(),
          nodes: currentMap,
        })
      );
    } catch (_) {
      // Игнорируем переполнение квоты localStorage
    }

    this._activeDiffIds = diffNodeIds;

    if (this._canvas) {
      this._canvas.renderSnapshot(this._currentSnapshot, {
        activeKinds: this.getActiveKinds(),
        activeStatuses: this.getActiveStatuses(),
        layoutName: this._el.layoutSelect ? this._el.layoutSelect.value : "fcose",
        diffNodeIds: this._activeDiffIds,
        preserveViewport: true,
      });
    }
  }

  /**
   * Включает или отключает периодический фоновый опрос сервера без сброса камеры.
   *
   * @param {boolean} isEnabled Флаг активности таймера авто-опроса.
   */
  toggleLivePoll(isEnabled) {
    if (this._livePollTimer) {
      clearInterval(this._livePollTimer);
      this._livePollTimer = null;
    }

    if (isEnabled) {
      this._livePollTimer = setInterval(() => {
        this.fetchSnapshot({ preserveViewport: true });
      }, TIMING_CONSTANTS.LIVE_POLL_INTERVAL_MS);
      this.showToast("Live auto-poll enabled (5s)", "info");
    } else {
      this.showToast("Live auto-poll stopped", "info");
    }
  }

  /**
   * Выполняет гибридный RAG-поиск по базе знаний и отображает результаты.
   *
   * @param {string} query Текст поискового запроса.
   */
  async executeSearch(query) {
    if (!this._el.searchMeta || !this._el.searchResultsList) {
      return;
    }

    try {
      this._el.searchMeta.textContent = "Querying knowledge base...";
      const workspace = this._el.workspaceInput ? this._el.workspaceInput.value.trim() : "";

      const queryParams = new URLSearchParams({
        q: query,
        workspace_path: workspace,
        top_k: String(TIMING_CONSTANTS.DEFAULT_SEARCH_TOP_K),
      });

      const response = await fetch(`${API_ENDPOINTS.KNOWLEDGE_SEARCH}?${queryParams.toString()}`);
      if (!response.ok) {
        throw new Error(`Search request failed with HTTP ${response.status}`);
      }

      const data = await response.json();
      const hits = Array.isArray(data.hits) ? data.hits : [];
      const backendMode = data.embedding_used ? "pgvector embedding" : "lexical fallback";
      this._el.searchMeta.textContent = `Found ${hits.length} hit${hits.length === 1 ? "" : "s"} (${backendMode})`;

      this._el.searchResultsList.innerHTML = "";

      if (hits.length === 0) {
        this._el.searchResultsList.innerHTML = `
          <div style="font-size:11px; color:var(--text-muted); padding:8px 4px;">
            No entities match query.
          </div>
        `;
        if (this._canvas) {
          this._canvas.highlightSearchHits([]);
        }
        return;
      }

      const hitIds = hits.map((h) => h.node_id);
      if (this._canvas) {
        this._canvas.highlightSearchHits(hitIds);
      }

      hits.forEach((hit) => {
        const item = document.createElement("div");
        item.className = "search-hit-item";
        item.dataset.nodeId = hit.node_id;

        const titleText = escapeHtml(hit.label || hit.node_id);
        const scoreVal = typeof hit.score === "number" ? hit.score.toFixed(2) : "0.00";
        const snippetText = escapeHtml(hit.snippet || "");

        item.innerHTML = `
          <div class="search-hit-header">
            <span class="search-hit-title" title="${titleText}">${titleText}</span>
            <span class="search-hit-score">${scoreVal}</span>
          </div>
          <div class="search-hit-snippet">${snippetText}</div>
        `;

        item.addEventListener("click", () => {
          if (this._canvas) {
            this._canvas.focusNode(hit.node_id);
          }
        });

        this._el.searchResultsList.appendChild(item);
      });
    } catch (err) {
      console.error("Hybrid search error:", err);
      this._el.searchMeta.textContent = `Search error: ${err.message}`;
      this.showToast(err.message, "error");
    }
  }

  /**
   * Сбрасывает поисковую выдачу и очищает подсветку узлов на графе.
   */
  clearSearch() {
    if (this._el.searchResultsList) {
      this._el.searchResultsList.innerHTML = "";
    }
    if (this._el.searchMeta) {
      this._el.searchMeta.textContent = "Type to perform hybrid search...";
    }
    if (this._canvas) {
      this._canvas.highlightSearchHits(null);
    }
  }

  /**
   * Открывает правый инспектор сущности и заполняет поля метаданных.
   *
   * @param {Object} nodeData Метаданные выбранного узла графа Cytoscape.
   */
  openInspector(nodeData) {
    if (!nodeData) {
      this.closeInspector();
      return;
    }

    this._selectedNodeData = nodeData;

    if (this._el.inspectorEmpty) {
      this._el.inspectorEmpty.classList.add("hidden");
    }
    if (this._el.inspectorContent) {
      this._el.inspectorContent.classList.add("visible");
    }

    if (this._el.inspTitle) {
      this._el.inspTitle.textContent = nodeData.label || nodeData.id;
    }
    if (this._el.inspId) {
      this._el.inspId.textContent = nodeData.id;
    }

    // Рендер бейджей классификации сущности
    if (this._el.inspBadges) {
      this._el.inspBadges.innerHTML = "";
      if (nodeData.kind) {
        const badge = document.createElement("span");
        badge.className = "insp-badge insp-badge-kind";
        badge.textContent = nodeData.kind;
        this._el.inspBadges.appendChild(badge);
      }
      if (nodeData.subtype) {
        const badge = document.createElement("span");
        badge.className = "insp-badge";
        badge.textContent = nodeData.subtype;
        this._el.inspBadges.appendChild(badge);
      }
      if (nodeData.status) {
        const badge = document.createElement("span");
        badge.className = `insp-badge insp-badge-status-${nodeData.status}`;
        badge.textContent = nodeData.status;
        this._el.inspBadges.appendChild(badge);
      }
      if (nodeData.scope) {
        const badge = document.createElement("span");
        badge.className = "insp-badge";
        badge.textContent = nodeData.scope;
        this._el.inspBadges.appendChild(badge);
      }
    }

    // Управление видимостью панели смены статуса (только для harness_component)
    if (this._el.inspActions) {
      const actionsWrapper = this._el.inspActions.parentElement;
      if (nodeData.kind === HARNESS_COMPONENT_KIND) {
        this._el.inspActions.classList.remove("hidden");
        if (actionsWrapper) {
          actionsWrapper.classList.remove("hidden");
        }
      } else {
        this._el.inspActions.classList.add("hidden");
        if (actionsWrapper) {
          actionsWrapper.classList.add("hidden");
        }
      }
    }

    // Теги стека
    if (this._el.inspTags) {
      this._el.inspTags.innerHTML = "";
      const tags = Array.isArray(nodeData.stack_tags) ? nodeData.stack_tags : [];
      if (tags.length === 0) {
        this._el.inspTags.innerHTML = `<span style="font-size:11px; color:var(--text-muted);">None</span>`;
      } else {
        tags.forEach((tag) => {
          const pill = document.createElement("span");
          pill.className = "tag-pill";
          pill.textContent = tag;
          this._el.inspTags.appendChild(pill);
        });
      }
    }

    // Метрики телеметрии
    if (this._el.inspMetrics) {
      this._el.inspMetrics.innerHTML = "";
      const metrics = nodeData.metrics && typeof nodeData.metrics === "object" ? nodeData.metrics : {};
      const entries = Object.entries(metrics);
      if (entries.length === 0) {
        this._el.inspMetrics.innerHTML = `
          <span style="font-size:11px; color:var(--text-muted); grid-column:span 2;">
            No telemetry metrics recorded.
          </span>
        `;
      } else {
        entries.forEach(([key, val]) => {
          const card = document.createElement("div");
          card.className = "metric-card";
          card.innerHTML = `
            <div class="metric-key">${escapeHtml(key)}</div>
            <div class="metric-val">${escapeHtml(String(val))}</div>
          `;
          this._el.inspMetrics.appendChild(card);
        });
      }
    }

    // Моноширинный блок содержимого / правил
    if (this._el.inspBody) {
      const content = nodeData.content_full || nodeData.content_preview || "No content body available.";
      this._el.inspBody.textContent = content;
    }
  }

  /**
   * Скрывает инспектор сущностей и показывает плейсхолдер пустого выбора.
   */
  closeInspector() {
    this._selectedNodeData = null;
    if (this._el.inspectorEmpty) {
      this._el.inspectorEmpty.classList.remove("hidden");
    }
    if (this._el.inspectorContent) {
      this._el.inspectorContent.classList.remove("visible");
    }
  }

  /**
   * Отправляет запрос на серверное изменение статуса компонента каталога Harness.
   *
   * @param {string} newStatus Новый статус компонента ('composed', 'probing', 'candidate', 'disabled').
   * @param {HTMLButtonElement} triggerBtn Кнопка, инициировавшая мутацию.
   */
  async handleStatusUpdate(newStatus, triggerBtn) {
    if (!this._selectedNodeData) {
      return;
    }

    const rawId = this._selectedNodeData.id;
    const slug = (this._selectedNodeData.metrics && this._selectedNodeData.metrics.slug)
      ? this._selectedNodeData.metrics.slug
      : rawId.replace(/^component:/, "");

    try {
      if (triggerBtn) {
        triggerBtn.disabled = true;
      }
      const response = await fetch(API_ENDPOINTS.COMPONENT_STATUS(slug), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ status: newStatus }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server returned HTTP ${response.status}`);
      }

      this.showToast(`Status updated to "${newStatus}"`, "success");
      await this.fetchSnapshot({ preserveViewport: true });

      // Повторный фокус на обновлённом узле
      if (this._canvas && rawId) {
        this._canvas.focusNode(rawId);
      }
    } catch (err) {
      console.error("Status update error:", err);
      this.showToast(`Update failed: ${err.message}`, "error");
    } finally {
      if (triggerBtn) {
        triggerBtn.disabled = false;
      }
    }
  }

  /**
   * Запускает процедуру консолидации семантических дубликатов каталога Harness.
   */
  async handleConsolidate() {
    if (!this._el.consolidateBtn) {
      return;
    }

    try {
      this._el.consolidateBtn.disabled = true;
      const response = await fetch(API_ENDPOINTS.CONSOLIDATE, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ apply: true, dry_run: false }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Consolidation failed: HTTP ${response.status}`);
      }

      const report = await response.json();
      const promoted = report.promoted_count ?? report.candidates_promoted ?? 0;
      const pruned = report.pruned_count ?? report.duplicates_pruned ?? 0;
      this.showToast(`Consolidation: ${promoted} promoted, ${pruned} pruned`, "success");

      await this.fetchSnapshot({ preserveViewport: true });
    } catch (err) {
      console.error("Consolidation error:", err);
      this.showToast(`Consolidation error: ${err.message}`, "error");
    } finally {
      this._el.consolidateBtn.disabled = false;
    }
  }

  /**
   * Отображает всплывающее тост-уведомление поверх полотна графа.
   *
   * @param {string} message Текст сообщения.
   * @param {"info"|"success"|"error"} [type="info"] Тип уведомления.
   */
  showToast(message, type = "info") {
    if (!this._el.canvasToast) {
      return;
    }

    this._el.canvasToast.textContent = message;
    this._el.canvasToast.className = "";

    if (type === "success") {
      this._el.canvasToast.classList.add("toast-success");
    } else if (type === "error") {
      this._el.canvasToast.classList.add("toast-error");
    }

    this._el.canvasToast.classList.add("visible");

    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => {
      if (this._el.canvasToast) {
        this._el.canvasToast.classList.remove("visible");
      }
    }, TIMING_CONSTANTS.TOAST_DURATION_MS);
  }
}

// Запуск инициализации приложения после готовности DOM дерева
if (typeof document !== "undefined") {
  const initBootstrap = () => {
    window.visualizerApp = new VisualizerApp();
    window.visualizerApp.init();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBootstrap);
  } else {
    initBootstrap();
  }
}
