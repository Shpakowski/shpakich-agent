"""Пакет статических шаблонов и сборщика HTML-интерфейса визуализатора (Layer 2 Core UI).

Объединяет HTML-каркас дашборда (`template.html`), кастомную тёмную дизайн-систему
(`styles.css`) и клиентские JavaScript-модули Cytoscape.js в единый автономный HTML-документ
для отдачи через FastAPI endpoint без необходимости рантайм-сборщиков на хосте.
"""

from __future__ import annotations

from pathlib import Path

# Имена файлов шаблона, стилей и клиентских скриптов визуализатора
TEMPLATE_FILENAME: str = "template.html"
STYLES_FILENAME: str = "styles.css"
GRAPH_CANVAS_JS_FILENAME: str = "graph_canvas.js"
APP_JS_FILENAME: str = "app.js"

# Маркеры инъекции статических ресурсов внутри HTML-шаблона
INJECT_STYLES_PLACEHOLDER: str = "/* __INJECT_STYLES_CSS__ */"
INJECT_GRAPH_CANVAS_JS_PLACEHOLDER: str = "/* __INJECT_GRAPH_CANVAS_JS__ */"
INJECT_APP_JS_PLACEHOLDER: str = "/* __INJECT_APP_JS__ */"


def render_visualizer_html() -> str:
    """Рендерит монолитный HTML-документ интерактивного визуализатора.

    Считывает базовый шаблон `template.html` и подставляет содержимое таблицы стилей `styles.css`
    и JavaScript-скриптов (`graph_canvas.js`, `app.js`), если они физически присутствуют на диске.
    Такой подход исключает необходимость сборки webpack/vite на хосте и обеспечивает мгновенную
    отдачу SPA через стандартный FastAPI HTMLResponse.

    Returns:
        Скомпилированная HTML-строка с инъецированными стилями и логикой визуализатора.

    Raises:
        FileNotFoundError: Если базовый файл шаблона `template.html` отсутствует в каталоге.
    """
    ui_dir: Path = Path(__file__).resolve().parent
    template_path: Path = ui_dir / TEMPLATE_FILENAME

    if not template_path.is_file():
        raise FileNotFoundError(f"Visualizer HTML template not found at {template_path}")

    html_content: str = template_path.read_text(encoding="utf-8")

    # Чтение CSS стилей дашборда
    styles_path: Path = ui_dir / STYLES_FILENAME
    styles_content: str = styles_path.read_text(encoding="utf-8") if styles_path.is_file() else ""

    # Чтение клиентских модулей JavaScript при их наличии
    canvas_js_path: Path = ui_dir / GRAPH_CANVAS_JS_FILENAME
    canvas_js_content: str = canvas_js_path.read_text(encoding="utf-8") if canvas_js_path.is_file() else ""

    app_js_path: Path = ui_dir / APP_JS_FILENAME
    app_js_content: str = app_js_path.read_text(encoding="utf-8") if app_js_path.is_file() else ""

    # Подстановка ресурсов в заполнители шаблона
    rendered: str = html_content.replace(INJECT_STYLES_PLACEHOLDER, styles_content)
    rendered = rendered.replace(INJECT_GRAPH_CANVAS_JS_PLACEHOLDER, canvas_js_content)
    rendered = rendered.replace(INJECT_APP_JS_PLACEHOLDER, app_js_content)

    return rendered


__all__ = [
    "APP_JS_FILENAME",
    "GRAPH_CANVAS_JS_FILENAME",
    "INJECT_APP_JS_PLACEHOLDER",
    "INJECT_GRAPH_CANVAS_JS_PLACEHOLDER",
    "INJECT_STYLES_PLACEHOLDER",
    "STYLES_FILENAME",
    "TEMPLATE_FILENAME",
    "render_visualizer_html",
]
