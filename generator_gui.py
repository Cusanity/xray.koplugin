#!/usr/bin/env python3
"""
PyQt6 GUI front-end for the X-Ray Generator.

A desktop interface around ``generator.py`` that lets you configure providers
and API keys, browse a Calibre library, run batch X-Ray analysis with live
progress and logs, and sync the results to a KOReader device.

The command-line workflow (``python generator.py <file>``) is unchanged; this
GUI simply reuses the same backend functions.

Usage:
    python generator_gui.py

Requires:
    pip install PyQt6
"""

from __future__ import annotations

import json
import os
import socket
import sys
import traceback
from typing import Any

# -----------------------------------------------------------------------------
# Make the sibling backend modules importable regardless of the current cwd.
# -----------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle — user data (config, output) lives next
    # to the .exe; bundled read-only assets are in sys._MEIPASS.
    _SCRIPT_DIR = os.path.dirname(sys.executable)
    _BUNDLE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = _SCRIPT_DIR

if _BUNDLE_DIR not in sys.path:
    sys.path.insert(0, _BUNDLE_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# Load .env early so ai_client picks up keys at import time (same as generator).
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_SCRIPT_DIR, ".env"))
except ImportError:
    pass

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

import ai_client
import calibre_browser
import generator
import retry_config
import webdav_sync
from retry_config import RetryChain, RetryChainOptions, RetryEntry
from epub_reader import get_sdr_name
from gui_i18n import AVAILABLE_LANGUAGES, tr

# =============================================================================
# Constants
# =============================================================================

PROVIDERS: list[tuple[str, str]] = [
    ("openai", "OpenAI-compatible"),
    ("claude", "Anthropic Claude"),
    ("groq", "Groq"),
    ("gemini", "Google Gemini"),
    ("deepseek", "DeepSeek"),
]

# (env_var, ai_client attribute or None, label, is_secret)
# OpenAI-compatible endpoint fields (base URL + key + optional extras). Unlike
# the cloud providers below, the endpoint is user-supplied, so it exposes the
# organization / project / custom-header options an OpenAI-compatible gateway
# or proxy may require.
OPENAI_FIELDS: list[tuple[str, str | None, str, bool]] = [
    ("XRAY_API_BASE", "API_BASE_URL", "Base URL", False),
    ("XRAY_API_KEY", "API_KEY", "API Key", True),
    ("XRAY_MODELS_ENDPOINT", "MODELS_ENDPOINT", "Models Endpoint", False),
]

# Cloud providers only need an API key (their base URLs are fixed).
CLOUD_KEY_FIELDS: list[tuple[str, str | None, str, bool]] = [
    ("CLAUDE_API_KEY", "CLAUDE_API_KEY", "Claude API Key", True),
    ("GROQ_API_KEY", "GROQ_API_KEY", "Groq API Key", True),
    ("GEMINI_API_KEY", "GEMINI_API_KEY", "Gemini API Key", True),
    ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY", "DeepSeek API Key", True),
]

# Union kept for the .env load/save and apply loops that iterate every field.
KEY_FIELDS: list[tuple[str, str | None, str, bool]] = OPENAI_FIELDS + CLOUD_KEY_FIELDS

# Maps provider key → env-var that holds the API key (excludes openai which is
# handled via XRAY_API_KEY and may not need one for local endpoints).
_PROVIDER_KEY_MAPPING: dict[str, str] = {
    "claude": "CLAUDE_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

_PROVIDER_ICON_COLORS: dict[str, str] = {
    "openai": "#1f7a8c",
    "claude": "#8e5c42",
    "groq": "#a24f9b",
    "gemini": "#2e7d32",
    "deepseek": "#1565c0",
}

_PROVIDER_ICON_TEXT: dict[str, str] = {
    "openai": "O",
    "claude": "C",
    "groq": "G",
    "gemini": "Ge",
    "deepseek": "D",
}

_PROVIDER_ICON_CACHE: dict[str, QIcon] = {}
# Prefer downloaded online assets (.ico/.png) before local placeholder SVGs.
_PROVIDER_ICON_EXTS = (".ico", ".png", ".svg")
_PROVIDER_ICON_SIZE = 18

DEFAULT_DEVICE_PORT = 8763


# =============================================================================
# Helpers
# =============================================================================


def output_json_path(epub_path: str) -> str:
    """Return the path where a book's xray_data.json lives."""
    sdr = get_sdr_name(epub_path)
    return os.path.join(generator.get_xray_base_dir(), sdr, "xray_analysis", "xray_data.json")


def read_progress(json_path: str) -> int | None:
    """Return analysis_progress from a checkpoint file, or None if absent."""
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return int(json.load(f).get("analysis_progress", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def status_text(progress: int | None) -> str:
    if progress is None:
        return "—"
    if progress >= 100:
        return tr("Complete")
    if progress <= 0:
        return tr("Pending")
    return tr("Partial {progress}%").format(progress=progress)


def fetch_models_for(api: str) -> list[str]:
    """Return the available model list for a provider."""
    if api == "claude":
        return ai_client.fetch_claude_models()
    if api == "groq":
        return ai_client.fetch_groq_models()
    if api == "gemini":
        return ai_client.fetch_gemini_models()
    if api == "deepseek":
        return ai_client.fetch_deepseek_models()
    return list(ai_client.AVAILABLE_MODELS)


def reset_model_caches() -> None:
    """Clear ai_client's per-provider model caches so a refresh refetches."""
    for name in (
        "_openai_models_cache",
        "_claude_models_cache",
        "_groq_models_cache",
        "_gemini_models_cache",
        "_deepseek_models_cache",
    ):
        if hasattr(ai_client, name):
            setattr(ai_client, name, None)


def _provider_icon_asset_path(provider_key: str) -> str | None:
    """Return the first matching provider icon asset path, if any."""
    rel_base = os.path.join("icons", "providers", provider_key)
    for root in (_SCRIPT_DIR, _BUNDLE_DIR):
        for ext in _PROVIDER_ICON_EXTS:
            path = os.path.join(root, rel_base + ext)
            if os.path.isfile(path):
                return path
    return None


def provider_icon(provider_key: str) -> QIcon:
    """Return a provider icon from assets, or a generated fallback."""
    cached = _PROVIDER_ICON_CACHE.get(provider_key)
    if cached is not None:
        return cached

    icon_path = _provider_icon_asset_path(provider_key)
    if icon_path:
        icon = QIcon(icon_path)
        if not icon.isNull():
            _PROVIDER_ICON_CACHE[provider_key] = icon
            return icon

    size = 18
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = QColor(_PROVIDER_ICON_COLORS.get(provider_key, "#607d8b"))
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size - 1, size - 1)

    painter.setPen(Qt.GlobalColor.white)
    font = painter.font()
    font.setBold(True)
    font.setPointSize(8)
    painter.setFont(font)
    painter.drawText(
        pix.rect(),
        Qt.AlignmentFlag.AlignCenter,
        _PROVIDER_ICON_TEXT.get(provider_key, "?"),
    )
    painter.end()

    icon = QIcon(pix)
    _PROVIDER_ICON_CACHE[provider_key] = icon
    return icon


def populate_provider_combo(combo: QComboBox) -> None:
    """Fill a provider combo with localized labels and provider icons."""
    combo.clear()
    combo.setIconSize(QSize(_PROVIDER_ICON_SIZE, _PROVIDER_ICON_SIZE))
    combo.setMinimumHeight(provider_row_height())
    for key, label in PROVIDERS:
        combo.addItem(provider_icon(key), tr(label), key)
    view = combo.view()
    if view is not None:
        view.setIconSize(QSize(_PROVIDER_ICON_SIZE, _PROVIDER_ICON_SIZE))
        view.setUniformItemSizes(True)


def provider_label(provider_key: str) -> str:
    """Return the localized display label for a provider key."""
    for key, label in PROVIDERS:
        if key == provider_key:
            return tr(label)
    return provider_key


def provider_table_item(provider_key: str) -> QTableWidgetItem:
    """Return a table item with provider icon + localized label."""
    item = QTableWidgetItem(provider_label(provider_key))
    item.setIcon(provider_icon(provider_key))
    return item


def provider_row_height() -> int:
    """Return a cross-platform row height that keeps provider icon/text aligned."""
    app = QApplication.instance()
    if app is None:
        return 24
    fm = QFontMetrics(QApplication.font())
    return max(24, _PROVIDER_ICON_SIZE + 8, fm.height() + 8)


def test_device(device: str) -> tuple[bool, str]:
    """Check whether a KOReader receiver port is reachable."""
    host, _, port_str = device.partition(":")
    port = int(port_str) if port_str else DEFAULT_DEVICE_PORT
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, tr("Port {host}:{port} is open.").format(host=host, port=port)
    except ConnectionRefusedError:
        return False, tr("Connection refused. Enable 'Receive from PC' on KOReader.")
    except OSError as e:
        return False, str(e)


# WebDAV status code -> (English label used as i18n key, hex color or None).
_WEBDAV_STATUS_META: dict[str, tuple[str, str | None]] = {
    webdav_sync.STATUS_SYNCED: ("Synced", "#2e7d32"),
    webdav_sync.STATUS_REMOTE_ONLY: ("On server", "#1565c0"),
    webdav_sync.STATUS_NOT_UPLOADED: ("Not uploaded", "#b58900"),
    webdav_sync.STATUS_DIFFERS: ("Differs", "#b58900"),
    webdav_sync.STATUS_ERROR: ("Error", "#c62828"),
    webdav_sync.STATUS_NONE: ("\u2014", None),
    webdav_sync.STATUS_UNCONFIGURED: ("", None),
    "checking": ("Checking\u2026", "#888888"),
}


def webdav_status_item(code: str) -> QTableWidgetItem:
    """Build a centered, colored table cell for a WebDAV status code."""
    label, color = _WEBDAV_STATUS_META.get(code, ("?", None))
    text = label if label in ("", "\u2014") else tr(label)
    item = QTableWidgetItem(text)
    if color:
        item.setForeground(QColor(color))
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


# =============================================================================
# Mouse-wheel guard
# =============================================================================


class _WheelGuard(QObject):
    """Stop the mouse wheel from changing spin box / combo box values.

    Hovering a ``QSpinBox``/``QDoubleSpinBox``/``QComboBox`` and scrolling would
    otherwise silently edit its value. This app-wide event filter swallows those
    wheel events and redirects them to the nearest scrolling ancestor, so the
    page still scrolls but the hovered field is left untouched.
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Wheel and isinstance(
            obj, (QAbstractSpinBox, QComboBox)
        ):
            area = obj.parent()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parent()
            if area is not None:
                QApplication.sendEvent(area.viewport(), event)
            return True
        return False


# =============================================================================
# Stdout redirection (captures backend print() output into the GUI log)
# =============================================================================


class _StdoutRedirector:
    """A minimal file-like object that forwards writes to a callback."""

    def __init__(self, emit) -> None:
        self._emit = emit

    def write(self, text: str) -> int:
        if text:
            self._emit(text)
        return len(text)

    def flush(self) -> None:  # noqa: D401 - file-like API
        pass


# =============================================================================
# Workers (run in QThreads)
# =============================================================================


class ProcessWorker(QObject):
    """Runs the batch analysis in a background thread."""

    progress = pyqtSignal(dict)
    log = pyqtSignal(str)
    book_started = pyqtSignal(str, int, int)   # path, index, total
    book_finished = pyqtSignal(str, bool, str)  # path, success, message
    pushed = pyqtSignal(str, bool)              # path, success
    webdav_pushed = pyqtSignal(str, bool)       # path, success
    finished = pyqtSignal()

    def __init__(self, paths: list[str], api: str, model: str,
                 device: str, auto_push: bool,
                 webdav_cfg: "webdav_sync.WebDavConfig | None" = None,
                 webdav_auto: bool = False) -> None:
        super().__init__()
        self._paths = paths
        self._api = api
        self._model = model
        self._device = device
        self._auto_push = auto_push
        self._webdav_cfg = webdav_cfg
        self._webdav_auto = webdav_auto
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        generator.request_stop()

    def run(self) -> None:
        generator.set_gui_hooks(progress_hook=self.progress.emit, fatal_raises=True)
        old_stdout = sys.stdout
        sys.stdout = _StdoutRedirector(self.log.emit)
        try:
            ai_client.configure(selected_api=self._api, selected_model=self._model)
            client = ai_client.create_client(self._api)
            if client is None:
                self.log.emit(
                    tr("ERROR: Could not create AI client. Check the API key/base URL.")
                    + "\n"
                )
                return

            total = len(self._paths)
            for i, path in enumerate(self._paths):
                if self._stop:
                    self.log.emit("\n" + tr("=== Stopped by user ===") + "\n")
                    break
                self.book_started.emit(path, i + 1, total)
                try:
                    generator.process_book(path, client, self._model)
                    self.book_finished.emit(path, True, "")
                    jp = output_json_path(path)
                    if self._auto_push and self._device:
                        if os.path.exists(jp):
                            generator.push_to_koreader(jp, self._device)
                            self.pushed.emit(path, True)
                    if (self._webdav_auto and self._webdav_cfg
                            and self._webdav_cfg.is_configured() and os.path.exists(jp)):
                        try:
                            webdav_sync.upload_book(
                                self._webdav_cfg, get_sdr_name(path), jp
                            )
                            self.webdav_pushed.emit(path, True)
                        except Exception as e:  # noqa: BLE001
                            self.log.emit(
                                tr("[{book}] WebDAV upload failed: {error}").format(
                                    book=os.path.basename(path), error=str(e)
                                ) + "\n"
                            )
                            self.webdav_pushed.emit(path, False)
                except generator.UserStoppedError:
                    self.book_finished.emit(path, False, "stopped by user")
                    self.log.emit("\n" + tr("=== Stopped by user ===") + "\n")
                    break
                except Exception as e:  # noqa: BLE001 - report and continue
                    self.log.emit(traceback.format_exc() + "\n")
                    self.book_finished.emit(path, False, str(e))
        finally:
            sys.stdout = old_stdout
            generator.set_gui_hooks(None, False)
            self.finished.emit()


class ModelFetchWorker(QObject):
    """Fetches the model list for a provider off the UI thread."""

    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, api: str) -> None:
        super().__init__()
        self._api = api

    def run(self) -> None:
        try:
            self.done.emit(fetch_models_for(self._api))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ScanWorker(QObject):
    """Scans a Calibre library and annotates each book with X-Ray status."""

    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, library: str) -> None:
        super().__init__()
        self._library = library

    def run(self) -> None:
        try:
            books = calibre_browser.scan_calibre_library(self._library)
            for b in books:
                b["progress"] = read_progress(output_json_path(b["epub_path"]))
            self.done.emit(books)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class WebDavStatusWorker(QObject):
    """Checks each book's local-vs-remote WebDAV state off the UI thread."""

    status = pyqtSignal(str, str)   # path, status code
    finished = pyqtSignal()

    def __init__(self, cfg: webdav_sync.WebDavConfig, paths: list[str]) -> None:
        super().__init__()
        self._cfg = cfg
        self._paths = paths

    def run(self) -> None:
        for path in self._paths:
            try:
                code = webdav_sync.book_status(
                    self._cfg, get_sdr_name(path), output_json_path(path)
                )
            except Exception:  # noqa: BLE001
                code = webdav_sync.STATUS_ERROR
            self.status.emit(path, code)
        self.finished.emit()


class WebDavOpWorker(QObject):
    """Uploads or downloads selected books to/from WebDAV."""

    status = pyqtSignal(str, str)   # path, post-op status code
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self, cfg: webdav_sync.WebDavConfig, paths: list[str], op: str
    ) -> None:
        super().__init__()
        self._cfg = cfg
        self._paths = paths
        self._op = op  # "upload" | "download"

    def run(self) -> None:
        for path in self._paths:
            sdr = get_sdr_name(path)
            jp = output_json_path(path)
            book = os.path.basename(path)
            try:
                if self._op == "upload":
                    webdav_sync.upload_book(self._cfg, sdr, jp)
                    self.log.emit(
                        tr("[{book}] uploaded to WebDAV.").format(book=book) + "\n"
                    )
                elif self._op == "download":
                    webdav_sync.download_book(self._cfg, sdr, jp)
                    self.log.emit(
                        tr("[{book}] downloaded from WebDAV.").format(book=book) + "\n"
                    )
                else:  # delete
                    webdav_sync.delete_book(self._cfg, sdr)
                    self.log.emit(
                        tr("[{book}] deleted from WebDAV.").format(book=book) + "\n"
                    )
            except Exception as e:  # noqa: BLE001
                key = (
                    "[{book}] WebDAV upload failed: {error}" if self._op == "upload"
                    else "[{book}] WebDAV download failed: {error}" if self._op == "download"
                    else "[{book}] WebDAV delete failed: {error}"
                )
                self.log.emit(tr(key).format(book=book, error=str(e)) + "\n")
            try:
                code = webdav_sync.book_status(self._cfg, sdr, jp)
            except Exception:  # noqa: BLE001
                code = webdav_sync.STATUS_ERROR
            self.status.emit(path, code)
        self.finished.emit()


# =============================================================================
# WebDAV folder browser dialog
# =============================================================================

_WEBDAV_FOLDER_PLACEHOLDER = "__loading__"


class WebDavFolderDialog(QDialog):
    """Interactive folder-tree browser for a WebDAV server.

    Opens at *cfg.root*, lazily loads sub-collections as the user expands
    nodes, and returns the selected folder URL via :meth:`selected_url`.
    """

    def __init__(self, cfg: "webdav_sync.WebDavConfig", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Browse WebDAV Folders"))
        self.resize(580, 440)
        self._cfg = cfg
        self._selected_url = cfg.root

        layout = QVBoxLayout(self)

        info = QLabel(
            tr("Select a folder to use as the WebDAV base URL for X-Ray sync.")
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel(tr("Selected:")))
        self._sel_edit = QLineEdit(cfg.root)
        self._sel_edit.setReadOnly(True)
        sel_row.addWidget(self._sel_edit, 1)
        layout.addLayout(sel_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemExpanded.connect(self._on_expand)
        self._tree.itemClicked.connect(self._on_click)
        layout.addWidget(self._tree, 1)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #666;")
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton(tr("Select This Folder"))
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._populate_root()

    def selected_url(self) -> str:
        """Return the URL the user last clicked, or the root if nothing was clicked."""
        return self._selected_url

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _populate_root(self) -> None:
        self._tree.clear()
        root_name = self._cfg.root.rstrip("/").rsplit("/", 1)[-1] or "/"
        root_item = QTreeWidgetItem([root_name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, self._cfg.root)
        self._tree.addTopLevelItem(root_item)
        self._load_children(root_item)   # blocking, but typically fast
        root_item.setExpanded(True)
        self._tree.setCurrentItem(root_item)

    def _add_placeholder(self, parent_item: QTreeWidgetItem) -> None:
        """Add a non-interactive child that triggers loading on first expand."""
        ph = QTreeWidgetItem(parent_item, ["\u2026"])
        ph.setData(0, Qt.ItemDataRole.UserRole, _WEBDAV_FOLDER_PLACEHOLDER)
        ph.setFlags(Qt.ItemFlag(0))  # not selectable / interactive

    def _on_expand(self, item: QTreeWidgetItem) -> None:
        """Replace a single placeholder child with the real sub-collections."""
        if (
            item.childCount() == 1
            and item.child(0).data(0, Qt.ItemDataRole.UserRole)
                == _WEBDAV_FOLDER_PLACEHOLDER
        ):
            item.takeChildren()
            self._load_children(item)

    def _on_click(self, item: QTreeWidgetItem) -> None:
        url = item.data(0, Qt.ItemDataRole.UserRole)
        if url and url != _WEBDAV_FOLDER_PLACEHOLDER:
            self._selected_url = url
            self._sel_edit.setText(url)

    def _load_children(self, item: QTreeWidgetItem) -> None:
        """PROPFIND depth=1 on *item*'s URL and populate child items."""
        url = item.data(0, Qt.ItemDataRole.UserRole)
        if not url or url == _WEBDAV_FOLDER_PLACEHOLDER:
            return
        self._status_lbl.setText(tr("Loading\u2026"))
        QApplication.processEvents()
        try:
            children = webdav_sync.list_children(self._cfg, url)
            for child_url, name in children:
                child_item = QTreeWidgetItem(item, [name])
                child_item.setData(0, Qt.ItemDataRole.UserRole, child_url)
                self._add_placeholder(child_item)  # deferred expand
            self._status_lbl.setText("")
        except webdav_sync.WebDavError as exc:
            self._status_lbl.setText(tr("Error: {msg}").format(msg=str(exc)))


# =============================================================================
# Add-Model Dialog
# =============================================================================

class _AddModelDialog(QDialog):
    """Popup for picking a provider + model to add to the retry chain."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Add Model"))
        self.setMinimumWidth(480)
        self._model_thread: QThread | None = None
        self._model_worker: "ModelFetchWorker | None" = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.provider_combo = QComboBox()
        populate_provider_combo(self.provider_combo)
        cur_idx = self.provider_combo.findData(parent.provider_combo.currentData())
        if cur_idx >= 0:
            self.provider_combo.setCurrentIndex(cur_idx)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow(tr("Provider:"), self.provider_combo)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(320)
        cur_model = parent.model_combo.currentText()
        for i in range(parent.model_combo.count()):
            self.model_combo.addItem(parent.model_combo.itemText(i))
        if cur_model:
            self.model_combo.setCurrentText(cur_model)
        self.refresh_btn = QPushButton(tr("Refresh"))
        self.refresh_btn.clicked.connect(self._refresh_models)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.refresh_btn)
        model_row_w = QWidget()
        model_row_w.setLayout(model_row)
        form.addRow(tr("Model:"), model_row_w)

        self.api_key_label = QLabel(tr("API Key:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(tr("Enter API key…"))
        form.addRow(self.api_key_label, self.api_key_edit)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._on_provider_changed()

    def _on_provider_changed(self) -> None:
        parent: "MainWindow" = self.parent()  # type: ignore[assignment]
        api = self.provider_combo.currentData()
        env_var = _PROVIDER_KEY_MAPPING.get(api, "")
        needs_key = bool(env_var)
        self.api_key_label.setVisible(needs_key)
        self.api_key_edit.setVisible(needs_key)
        if needs_key:
            existing = parent._key_edits.get(env_var)
            self.api_key_edit.setText(existing.text().strip() if existing else "")
        # Reload model list from cache; if empty, kick off a background fetch.
        cached = fetch_models_for(api)
        if cached:
            current = self.model_combo.currentText()
            self.model_combo.clear()
            self.model_combo.addItems(cached)
            if current in cached:
                self.model_combo.setCurrentText(current)
            else:
                self.model_combo.setCurrentIndex(0)
        else:
            self.model_combo.clear()
            self._refresh_models()

    def _push_key_to_parent(self) -> None:
        """Copy the entered API key into the parent's key_edits field."""
        parent: "MainWindow" = self.parent()  # type: ignore[assignment]
        api = self.provider_combo.currentData()
        env_var = _PROVIDER_KEY_MAPPING.get(api, "")
        if env_var and env_var in parent._key_edits:
            key_text = self.api_key_edit.text().strip()
            if key_text:
                parent._key_edits[env_var].setText(key_text)

    def _refresh_models(self) -> None:
        if self._model_thread is not None and self._model_thread.isRunning():
            return
        parent: "MainWindow" = self.parent()  # type: ignore[assignment]
        self._push_key_to_parent()
        parent._apply_config()
        reset_model_caches()
        api = self.provider_combo.currentData()
        self.refresh_btn.setEnabled(False)
        self._model_thread = QThread()
        self._model_worker = ModelFetchWorker(api)
        self._model_worker.moveToThread(self._model_thread)
        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.done.connect(self._on_models_done)
        self._model_worker.failed.connect(self._on_models_failed)
        self._model_worker.done.connect(self._model_thread.quit)
        self._model_worker.failed.connect(self._model_thread.quit)
        self._model_thread.finished.connect(self._cleanup_model_thread)
        self._model_thread.start()

    def _on_models_done(self, models: list) -> None:
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current in models:
            self.model_combo.setCurrentText(current)
        elif models:
            self.model_combo.setCurrentIndex(0)

    def _on_models_failed(self, msg: str) -> None:
        QMessageBox.warning(self, tr("Model fetch failed"), msg)

    def _cleanup_model_thread(self) -> None:
        self.refresh_btn.setEnabled(True)
        if self._model_thread is not None:
            self._model_thread.wait()
        self._model_thread = None
        self._model_worker = None

    def selected_provider(self) -> str:
        return self.provider_combo.currentData() or "openai"

    def selected_model(self) -> str:
        return self.model_combo.currentText().strip()


class SetupWizard(QWizard):
    """Guided first-time setup for core app configuration."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Setup Wizard"))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.resize(760, 520)

        self._parent = parent
        self._webdav_authenticated = False

        self.addPage(self._build_provider_page())
        self.addPage(self._build_library_page())
        self.addPage(self._build_sync_page())
        self.addPage(self._build_finish_page())

    def _build_provider_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(tr("Provider & Model"))
        page.setSubTitle(tr("Choose your primary provider, model, and API key."))

        form = QFormLayout(page)
        self.w_provider_combo = QComboBox()
        populate_provider_combo(self.w_provider_combo)
        cur_idx = self.w_provider_combo.findData(self._parent.provider_combo.currentData())
        if cur_idx >= 0:
            self.w_provider_combo.setCurrentIndex(cur_idx)
        self.w_provider_combo.currentIndexChanged.connect(self._on_wizard_provider_changed)
        form.addRow(tr("Provider:"), self.w_provider_combo)

        model_row = QHBoxLayout()
        self.w_model_combo = QComboBox()
        self.w_model_combo.setEditable(True)
        self.w_model_combo.setMinimumWidth(360)
        self.w_model_refresh_btn = QPushButton(tr("Refresh"))
        self.w_model_refresh_btn.clicked.connect(self._wizard_refresh_models)
        model_row.addWidget(self.w_model_combo, 1)
        model_row.addWidget(self.w_model_refresh_btn)
        model_row_w = QWidget()
        model_row_w.setLayout(model_row)
        form.addRow(tr("Model:"), model_row_w)

        self.w_api_key_label = QLabel(tr("API Key:"))
        self.w_api_key_edit = QLineEdit()
        self.w_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.w_api_key_edit.setPlaceholderText(tr("Enter API key…"))
        key_row = QHBoxLayout()
        key_row.addWidget(self.w_api_key_edit, 1)
        self.w_show_key = QCheckBox(tr("Show"))
        self.w_show_key.stateChanged.connect(
            lambda state: self.w_api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if state else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.w_show_key)
        key_row_w = QWidget()
        key_row_w.setLayout(key_row)
        form.addRow(self.w_api_key_label, key_row_w)

        self.w_provider_hint = QLabel("")
        self.w_provider_hint.setStyleSheet("color: #666;")
        self.w_provider_hint.setWordWrap(True)
        form.addRow("", self.w_provider_hint)

        self._on_wizard_provider_changed()
        return page

    def _build_library_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(tr("Library & Advanced"))
        page.setSubTitle(tr("Set your Calibre library and output settings."))

        form = QFormLayout(page)
        lib_row = QHBoxLayout()
        self.w_calibre_edit = QLineEdit(self._parent.calibre_edit.text().strip())
        lib_scan = QPushButton(tr("Scan"))
        lib_scan.clicked.connect(self._wizard_auto_detect_calibre)
        lib_browse = QPushButton(tr("Browse…"))
        lib_browse.clicked.connect(self._wizard_browse_calibre)
        lib_row.addWidget(self.w_calibre_edit, 1)
        lib_row.addWidget(lib_scan)
        lib_row.addWidget(lib_browse)
        lib_row_w = QWidget()
        lib_row_w.setLayout(lib_row)
        form.addRow(tr("Calibre Library:"), lib_row_w)

        out_row = QHBoxLayout()
        self.w_output_edit = QLineEdit(self._parent.xray_output_edit.text().strip())
        self.w_output_edit.setPlaceholderText(tr("Default: <app folder>/xray"))
        out_browse = QPushButton(tr("Browse…"))
        out_browse.clicked.connect(self._wizard_browse_output)
        out_row.addWidget(self.w_output_edit, 1)
        out_row.addWidget(out_browse)
        out_row_w = QWidget()
        out_row_w.setLayout(out_row)
        form.addRow(tr("X-Ray Output Folder:"), out_row_w)

        self.w_temp_spin = QDoubleSpinBox()
        self.w_temp_spin.setRange(0.0, 2.0)
        self.w_temp_spin.setSingleStep(0.1)
        self.w_temp_spin.setValue(self._parent.temp_spin.value())
        form.addRow(tr("Temperature:"), self.w_temp_spin)

        self.w_lang_combo = QComboBox()
        for code, name in AVAILABLE_LANGUAGES:
            self.w_lang_combo.addItem(name, code)
        lang_idx = self.w_lang_combo.findData(self._parent.lang_combo.currentData())
        if lang_idx >= 0:
            self.w_lang_combo.setCurrentIndex(lang_idx)
        form.addRow(tr("Language:"), self.w_lang_combo)
        return page

    def _build_sync_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(tr("Sync"))
        page.setSubTitle(tr("Optional: configure KOReader device and WebDAV sync."))

        layout = QVBoxLayout(page)

        device_box = QGroupBox(tr("KOReader Device"))
        device_form = QFormLayout(device_box)
        self.w_device_edit = QLineEdit(self._parent.device_edit.text().strip())
        self.w_device_edit.setPlaceholderText("192.168.1.42  or  192.168.1.42:8763")
        device_form.addRow(tr("Device IP[:port]:"), self.w_device_edit)
        self.w_autopush_chk = QCheckBox(tr("Push results automatically after each book"))
        self.w_autopush_chk.setChecked(self._parent.autopush_chk.isChecked())
        device_form.addRow("", self.w_autopush_chk)
        layout.addWidget(device_box)

        webdav_box = QGroupBox(tr("WebDAV Cloud Sync"))
        webdav_form = QFormLayout(webdav_box)
        self.w_webdav_url_edit = QLineEdit(self._parent.webdav_url_edit.text().strip())
        webdav_form.addRow(tr("Server URL:"), self.w_webdav_url_edit)
        self.w_webdav_user_edit = QLineEdit(self._parent.webdav_user_edit.text().strip())
        webdav_form.addRow(tr("Username:"), self.w_webdav_user_edit)
        self.w_webdav_pass_edit = QLineEdit(self._parent.webdav_pass_edit.text())
        self.w_webdav_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.w_webdav_pass_edit, 1)
        pass_show = QCheckBox(tr("Show"))
        pass_show.stateChanged.connect(
            lambda state: self.w_webdav_pass_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if state else QLineEdit.EchoMode.Password
            )
        )
        pass_row.addWidget(pass_show)
        pass_row_w = QWidget()
        pass_row_w.setLayout(pass_row)
        webdav_form.addRow(tr("Password:"), pass_row_w)

        self.w_webdav_status_label = QLabel("")
        self.w_webdav_status_label.setWordWrap(True)
        webdav_form.addRow(tr("Status:"), self.w_webdav_status_label)

        test_row = QHBoxLayout()
        self.w_webdav_test_btn = QPushButton(tr("Test Connection"))
        self.w_webdav_test_btn.clicked.connect(self._wizard_test_webdav)
        test_row.addWidget(self.w_webdav_test_btn)
        self.w_webdav_browse_btn = QPushButton(tr("Choose Path"))
        self.w_webdav_browse_btn.clicked.connect(self._wizard_browse_webdav_folder)
        test_row.addWidget(self.w_webdav_browse_btn)
        test_row.addStretch(1)
        test_row_w = QWidget()
        test_row_w.setLayout(test_row)
        webdav_form.addRow("", test_row_w)

        self.w_webdav_auto_chk = QCheckBox(tr("Upload to WebDAV automatically after each book"))
        self.w_webdav_auto_chk.setChecked(self._parent.webdav_autopush_chk.isChecked())
        webdav_form.addRow("", self.w_webdav_auto_chk)
        layout.addWidget(webdav_box)

        self.w_webdav_url_edit.textChanged.connect(self._wizard_on_webdav_credentials_edited)
        self.w_webdav_user_edit.textChanged.connect(self._wizard_on_webdav_credentials_edited)
        self.w_webdav_pass_edit.textChanged.connect(self._wizard_on_webdav_credentials_edited)
        self._wizard_on_webdav_credentials_edited()

        layout.addStretch(1)
        return page

    def _build_finish_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(tr("Review & Apply"))
        page.setSubTitle(tr("Review your choices, then click Finish to apply."))

        layout = QVBoxLayout(page)
        self.w_summary = QLabel("")
        self.w_summary.setWordWrap(True)
        self.w_summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.w_summary)
        layout.addStretch(1)
        return page

    def initializePage(self, page_id: int) -> None:  # noqa: N802 - Qt override
        super().initializePage(page_id)
        if page_id != 3:
            return

        provider = self.w_provider_combo.currentText()
        model = self.w_model_combo.currentText().strip() or tr("(not set)")
        library = self.w_calibre_edit.text().strip() or tr("(not set)")
        output = self.w_output_edit.text().strip() or tr("(default)")
        device = self.w_device_edit.text().strip() or tr("(not set)")
        webdav = self.w_webdav_url_edit.text().strip() or tr("(not set)")

        lines = [
            tr("Provider: {value}").format(value=provider),
            tr("Model: {value}").format(value=model),
            tr("Calibre Library: {value}").format(value=library),
            tr("X-Ray Output Folder: {value}").format(value=output),
            tr("Device IP[:port]: {value}").format(value=device),
            tr("Server URL: {value}").format(value=webdav),
        ]
        self.w_summary.setText("\n".join(lines))

    def _on_wizard_provider_changed(self) -> None:
        provider = self.w_provider_combo.currentData() or "openai"
        models = fetch_models_for(provider)
        current = self.w_model_combo.currentText().strip()
        self.w_model_combo.blockSignals(True)
        self.w_model_combo.clear()
        self.w_model_combo.addItems(models)
        self.w_model_combo.setCurrentText(current or self._parent.model_combo.currentText())
        self.w_model_combo.blockSignals(False)

        env_var = _PROVIDER_KEY_MAPPING.get(provider, "")
        needs_key = bool(env_var)
        self.w_api_key_label.setVisible(needs_key)
        self.w_api_key_edit.setVisible(needs_key)
        self.w_show_key.setVisible(needs_key)
        if needs_key and env_var in self._parent._key_edits:
            self.w_api_key_edit.setText(self._parent._key_edits[env_var].text().strip())
            self.w_provider_hint.setText(tr("Set the API key for this provider."))
        else:
            self.w_api_key_edit.clear()
            self.w_provider_hint.setText(tr("OpenAI-compatible endpoints may work without an API key."))

    def _wizard_refresh_models(self) -> None:
        provider = self.w_provider_combo.currentData() or "openai"
        env_var = _PROVIDER_KEY_MAPPING.get(provider, "")
        if env_var and env_var in self._parent._key_edits:
            self._parent._key_edits[env_var].setText(self.w_api_key_edit.text().strip())
        self._parent._apply_config()
        reset_model_caches()
        self._on_wizard_provider_changed()

    def _wizard_auto_detect_calibre(self) -> None:
        found = calibre_browser.find_calibre_libraries()
        if not found:
            QMessageBox.information(
                self,
                tr("No Calibre Library Found"),
                tr("Could not find a Calibre library in common locations.\nUse Browse… to select it manually."),
            )
            return
        if len(found) == 1:
            self.w_calibre_edit.setText(found[0])
            return
        item, ok = QInputDialog.getItem(
            self,
            tr("Select Calibre Library"),
            tr("Multiple Calibre libraries found. Select one:"),
            found,
            0,
            False,
        )
        if ok and item:
            self.w_calibre_edit.setText(item)

    def _wizard_browse_calibre(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("Select Calibre Library"), self.w_calibre_edit.text() or _SCRIPT_DIR
        )
        if d:
            self.w_calibre_edit.setText(d)

    def _wizard_browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            tr("Select X-Ray Output Folder"),
            self.w_output_edit.text() or generator.get_xray_base_dir(),
        )
        if d:
            self.w_output_edit.setText(d)

    def _wizard_webdav_config(self) -> webdav_sync.WebDavConfig:
        """Build a WebDavConfig from the wizard Sync-page fields."""
        return webdav_sync.WebDavConfig(
            base_url=self.w_webdav_url_edit.text().strip(),
            username=self.w_webdav_user_edit.text().strip(),
            password=self.w_webdav_pass_edit.text(),
        )

    def _wizard_on_webdav_credentials_edited(self) -> None:
        """Reset wizard WebDAV login state whenever URL/user/password changes."""
        self._webdav_authenticated = False
        self.w_webdav_status_label.setText(tr("Not configured."))

    def _wizard_test_webdav(self) -> None:
        """Test WebDAV credentials and unlock folder browsing on success."""
        cfg = self._wizard_webdav_config()
        if not cfg.is_configured():
            self.w_webdav_status_label.setText(tr("Enter a WebDAV server URL first."))
            return
        self.w_webdav_status_label.setText(tr("Testing…"))
        QApplication.processEvents()
        ok, msg = webdav_sync.test_connection(cfg)
        self._webdav_authenticated = ok
        self.w_webdav_status_label.setText(("✓ " if ok else "✗ ") + msg)

    def _wizard_browse_webdav_folder(self) -> None:
        """Open the WebDAV folder browser for the wizard URL/credential fields."""
        cfg = self._wizard_webdav_config()
        if not cfg.is_configured():
            QMessageBox.information(
                self,
                tr("Browse WebDAV Folders"),
                tr("Enter a WebDAV server URL and credentials before browsing."),
            )
            return
        dlg = WebDavFolderDialog(cfg, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.w_webdav_url_edit.setText(dlg.selected_url())

    def apply_to_parent(self) -> None:
        provider = self.w_provider_combo.currentData() or "openai"
        provider_idx = self._parent.provider_combo.findData(provider)
        if provider_idx >= 0:
            self._parent.provider_combo.setCurrentIndex(provider_idx)
        self._parent.model_combo.setCurrentText(self.w_model_combo.currentText().strip())

        env_var = _PROVIDER_KEY_MAPPING.get(provider, "")
        if env_var and env_var in self._parent._key_edits:
            self._parent._key_edits[env_var].setText(self.w_api_key_edit.text().strip())

        self._parent.calibre_edit.setText(self.w_calibre_edit.text().strip())
        self._parent.xray_output_edit.setText(self.w_output_edit.text().strip())
        self._parent.temp_spin.setValue(self.w_temp_spin.value())

        lang = self.w_lang_combo.currentData()
        lang_idx = self._parent.lang_combo.findData(lang)
        if lang_idx >= 0:
            self._parent.lang_combo.blockSignals(True)
            self._parent.lang_combo.setCurrentIndex(lang_idx)
            self._parent.lang_combo.blockSignals(False)
            self._parent._prefs["gui_lang"] = lang

        self._parent.device_edit.setText(self.w_device_edit.text().strip())
        self._parent.autopush_chk.setChecked(self.w_autopush_chk.isChecked())
        self._parent.webdav_url_edit.setText(self.w_webdav_url_edit.text().strip())
        self._parent.webdav_user_edit.setText(self.w_webdav_user_edit.text().strip())
        self._parent.webdav_pass_edit.setText(self.w_webdav_pass_edit.text())
        self._parent.webdav_autopush_chk.setChecked(self.w_webdav_auto_chk.isChecked())

        self._parent._apply_config()
        self._parent._prefs["setup_wizard_launched"] = True
        self._parent._prefs["setup_wizard_seen"] = True
        calibre_browser._save_preferences(self._parent._prefs)
        self._parent._refresh_setup_wizard_menu_action()


# =============================================================================
# Cost Summary (LiteLLM price catalog)
# =============================================================================

LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main"
    "/model_prices_and_context_window.json"
)

# Maps our provider IDs → the prefixes LiteLLM uses in its catalog.
_LITELLM_PROVIDER_PREFIXES: dict[str, list[str]] = {
    "openai":   ["", "openai/"],
    "claude":   ["", "anthropic/", "claude/"],
    "groq":     ["groq/", ""],
    "gemini":   ["gemini/", "vertex_ai/gemini/", ""],
    "deepseek": ["deepseek/", ""],
}


def _lookup_litellm_price(
    catalog: dict, provider: str, model: str
) -> tuple[float, float] | None:
    """Return (input_cost_per_token, output_cost_per_token) or None."""
    # Google API returns model IDs with a "models/" prefix; LiteLLM omits it.
    if provider == "gemini" and model.startswith("models/"):
        model = model[len("models/"):]
    prefixes = _LITELLM_PROVIDER_PREFIXES.get(provider, ["", f"{provider}/"])
    for prefix in prefixes:
        entry = catalog.get(f"{prefix}{model}")
        if entry and isinstance(entry, dict):
            inp = float(entry.get("input_cost_per_token") or 0)
            out = float(entry.get("output_cost_per_token") or 0)
            return inp, out
    return None


class _PriceFetcher(QObject):
    """Fetches the LiteLLM model-price catalog in a background thread."""

    done = pyqtSignal(dict, str)  # price catalog dict (or {} on failure), last-modified date

    def run(self) -> None:
        try:
            import urllib.request

            with urllib.request.urlopen(LITELLM_PRICES_URL, timeout=10) as resp:
                last_modified = resp.headers.get("Last-Modified", "")
                data = json.loads(resp.read().decode("utf-8"))
            self.done.emit(data, last_modified)
        except Exception:  # noqa: BLE001
            self.done.emit({}, "")


class CostSummaryDialog(QDialog):
    """Shows per-model token usage and estimated cost after a batch run.

    Prices are fetched asynchronously from the LiteLLM community catalog
    (https://github.com/BerriAI/litellm) so the dialog appears immediately
    with token counts and fills in the cost column once the data arrives.
    """

    def __init__(
        self,
        usage: dict[str, dict[str, int]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Batch Complete – Token Usage & Cost"))
        self.setMinimumWidth(640)
        self._usage = usage  # {provider/model: {prompt: int, completion: int}}
        self._fetch_thread: QThread | None = None
        self._fetcher: _PriceFetcher | None = None

        layout = QVBoxLayout(self)

        hdr = QLabel(tr("Processing complete. Token usage summary:"))
        layout.addWidget(hdr)

        # Sort rows: provider first, then model name.
        self._rows: list[tuple[str, str, dict[str, int]]] = []
        for key, counts in sorted(usage.items()):
            prov, _, mdl = key.partition("/")
            if not mdl:
                mdl, prov = prov, "openai"
            self._rows.append((prov, mdl, counts))

        self._table = QTableWidget(len(self._rows), 6)
        self._table.setIconSize(QSize(_PROVIDER_ICON_SIZE, _PROVIDER_ICON_SIZE))
        self._table.setHorizontalHeaderLabels([
            tr("Provider"),
            tr("Model"),
            tr("Prompt Tokens"),
            tr("Completion Tokens"),
            tr("Total Chars"),
            tr("Est. Cost (USD)"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(provider_row_height())
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        for r, (prov, mdl, counts) in enumerate(self._rows):
            self._table.setItem(r, 0, provider_table_item(prov))
            self._table.setItem(r, 1, QTableWidgetItem(mdl))
            self._table.setItem(r, 2, QTableWidgetItem(f"{counts['prompt']:,}"))
            self._table.setItem(r, 3, QTableWidgetItem(f"{counts['completion']:,}"))
            chars = counts.get("chars", 0)
            self._table.setItem(r, 4, QTableWidgetItem(f"{chars:,}" if chars else "—"))
            self._table.setItem(r, 5, QTableWidgetItem("…"))

        layout.addWidget(self._table)

        self._status_label = QLabel(tr("Fetching prices from LiteLLM catalog…"))
        self._status_label.setStyleSheet("color: #666; font-size: 11px;")
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._status_label)

        self._total_label = QLabel("")
        self._total_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._total_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)

        self._start_fetch()

    # ------------------------------------------------------------------ fetch

    def _start_fetch(self) -> None:
        self._fetch_thread = QThread()
        self._fetcher = _PriceFetcher()
        self._fetcher.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetcher.run)
        self._fetcher.done.connect(self._on_prices_loaded)
        self._fetcher.done.connect(self._fetch_thread.quit)
        self._fetch_thread.finished.connect(self._cleanup_fetch_thread)
        self._fetch_thread.start()

    def _cleanup_fetch_thread(self) -> None:
        if self._fetch_thread is not None:
            self._fetch_thread.wait()
        self._fetch_thread = None
        self._fetcher = None

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            self._fetch_thread.wait()
        super().closeEvent(event)

    # ---------------------------------------------------------------- pricing

    def _on_prices_loaded(self, catalog: dict, last_modified: str) -> None:
        if not catalog:
            self._status_label.setText(
                tr("Could not fetch prices (offline?). Token counts are still accurate.")
            )
            for r in range(self._table.rowCount()):
                self._table.item(r, 5).setText(tr("N/A"))
            return

        total_cost = 0.0
        any_known = False
        for r, (prov, mdl, counts) in enumerate(self._rows):
            price = _lookup_litellm_price(catalog, prov, mdl)
            if price:
                inp_rate, out_rate = price
                cost = counts["prompt"] * inp_rate + counts["completion"] * out_rate
                total_cost += cost
                any_known = True
                self._table.item(r, 5).setText(f"${cost:.6f}")  # noqa: i18n
            else:
                self._table.item(r, 5).setText(tr("unknown"))

        date_str = ""
        if last_modified:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(last_modified)
                date_str = tr(", updated {date}").format(date=dt.strftime('%Y-%m-%d'))
            except Exception:  # noqa: BLE001
                pass
        catalog_url = "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json"
        self._status_label.setText(
            tr('Prices sourced from <a href="{url}">LiteLLM community catalog</a>{date}').format(
                url=catalog_url, date=date_str
            )
        )
        if any_known:
            self._total_label.setText(
                tr("Total estimated cost: ${cost}").format(
                    cost=f"{total_cost:.4f}"
                )
            )


# =============================================================================
# Chain info label (lazy tooltip on the Generate button)
# =============================================================================


class _ChainInfoLabel(QLabel):
    """Small 'ⓘ' label that refreshes its tooltip from a callback on hover."""

    def __init__(self, tooltip_fn, parent=None) -> None:
        super().__init__("ⓘ", parent)
        self._tooltip_fn = tooltip_fn
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "color: #1565c0; font-weight: bold; font-size: 13px; padding: 0 2px;"
        )

    def event(self, e: QEvent) -> bool:  # type: ignore[override]
        if e.type() == QEvent.Type.ToolTip:
            self.setToolTip(self._tooltip_fn())
        return super().event(e)


# =============================================================================
# Main Window
# =============================================================================


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._prefs: dict[str, Any] = calibre_browser._load_preferences()

        # Activate the UI language before any widgets/strings are created.
        import gui_i18n
        gui_i18n.set_language(self._prefs.get("gui_lang"))

        self.setWindowTitle(tr("X-Ray Generator"))
        self.resize(1100, 760)
        # Allow shrinking on short screens; tabs scroll their own content.
        self.setMinimumSize(760, 420)

        # Thread/worker references (kept alive to avoid GC).
        self._proc_thread: QThread | None = None
        self._proc_worker: ProcessWorker | None = None
        self._model_thread: QThread | None = None
        self._model_worker: ModelFetchWorker | None = None
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._webdav_thread: QThread | None = None
        self._webdav_worker: QObject | None = None
        self._price_thread: QThread | None = None
        self._price_fetcher: _PriceFetcher | None = None
        self._price_catalog: dict = {}
        self._webdav_authenticated = False
        self._wizard_menu_action: QAction | None = None

        self._key_edits: dict[str, QLineEdit] = {}
        self._extra_books: list[str] = []  # non-Calibre EPUBs added manually

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_config_tab(), tr("Configuration"))
        self.tabs.addTab(self._build_books_tab(), tr("Books"))
        self.tabs.addTab(self._build_progress_tab(), tr("Progress"))
        self.tabs.addTab(self._build_sync_tab(), tr("Sync"))
        self.tabs.addTab(self._build_results_tab(), tr("Results"))

        self._build_menu()
        self.statusBar().showMessage(tr("Ready"))

        self._load_prefs_into_ui()
        self._on_provider_changed()
        self._maybe_offer_setup_wizard()

        # Auto-scan the Calibre library at launch so the book list is ready
        # without a manual click (only when a valid library path is set).
        # If no path is configured yet, try to auto-detect one.
        lib = self.calibre_edit.text().strip()
        if not lib:
            found = calibre_browser.find_calibre_libraries()
            if found:
                lib = found[0]
                self.calibre_edit.setText(lib)
        if lib and os.path.isdir(lib):
            self._scan_library()

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu(tr("&File"))
        act_env_load = QAction(tr("Load settings from .env"), self)
        act_env_load.triggered.connect(self._load_env)
        act_env_save = QAction(tr("Save settings to .env"), self)
        act_env_save.triggered.connect(self._save_env)
        act_quit = QAction(tr("Quit"), self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_env_load)
        file_menu.addAction(act_env_save)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        self._wizard_menu_action = self.menuBar().addAction("")
        self._wizard_menu_action.triggered.connect(self._open_setup_wizard)
        self._refresh_setup_wizard_menu_action()

    # ------------------------------------------------------------ config tab
    def _add_key_row(
        self, form: QFormLayout, env_var: str, label: str, is_secret: bool
    ) -> None:
        """Add one key/endpoint line-edit row to a form, tracking it for save."""
        edit = QLineEdit()
        edit.setText(os.environ.get(env_var, ""))
        self._key_edits[env_var] = edit
        if is_secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            show = QCheckBox(tr("Show"))
            show.stateChanged.connect(
                lambda state, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if state else QLineEdit.EchoMode.Password
                )
            )
            row.addWidget(show)
            rw = QWidget()
            rw.setLayout(row)
            form.addRow(f"{tr(label)}:", rw)
        else:
            form.addRow(f"{tr(label)}:", edit)

    def _build_config_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        # Provider + model
        prov_box = QGroupBox(tr("Provider & Model"))
        prov_form = QFormLayout(prov_box)
        self.provider_combo = QComboBox()
        populate_provider_combo(self.provider_combo)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(320)
        self.refresh_models_btn = QPushButton(tr("Refresh"))
        self.refresh_models_btn.clicked.connect(self._refresh_models)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.refresh_models_btn)
        model_row_w = QWidget()
        model_row_w.setLayout(model_row)

        prov_form.addRow(tr("Provider:"), self.provider_combo)
        prov_form.addRow(tr("Model:"), model_row_w)
        self.provider_hint = QLabel("")
        self.provider_hint.setStyleSheet("color: #b58900;")
        prov_form.addRow("", self.provider_hint)
        # Keep prov_box alive (holds combo widgets used by prefs/env/fallback)
        # but do NOT add it to the visible layout — it lives in the popup instead.
        self._prov_box = prov_box

        # Retry / fallback chain (the source of truth for retries & fallback).
        layout.addWidget(self._build_chain_box())

        # Per-provider concurrency / chunk-size limits.
        layout.addWidget(self._build_limits_box())

        # OpenAI-compatible endpoint (URL + key + optional org/project/headers)
        oai_box = QGroupBox(tr("OpenAI-compatible Endpoint"))
        oai_form = QFormLayout(oai_box)
        for env_var, _attr, label, is_secret in OPENAI_FIELDS:
            self._add_key_row(oai_form, env_var, label, is_secret)
        self._headers_edit = QPlainTextEdit()
        self._headers_edit.setPlaceholderText(
            tr("One per line: Header-Name: value   (or a JSON object)")
        )
        self._headers_edit.setPlainText(os.environ.get("XRAY_API_HEADERS", ""))
        self._headers_edit.setFixedHeight(70)
        oai_form.addRow(f"{tr('Custom Headers')}:", self._headers_edit)
        layout.addWidget(oai_box)

        # Cloud provider API keys (base URLs are fixed by the provider)
        keys_box = QGroupBox(tr("Cloud Provider API Keys"))
        keys_form = QFormLayout(keys_box)
        for env_var, _attr, label, is_secret in CLOUD_KEY_FIELDS:
            self._add_key_row(keys_form, env_var, label, is_secret)
        layout.addWidget(keys_box)

        # Calibre library + advanced
        misc_box = QGroupBox(tr("Library & Advanced"))
        misc_form = QFormLayout(misc_box)
        lib_row = QHBoxLayout()
        self.calibre_edit = QLineEdit(os.environ.get("CALIBRE_LIBRARY", ""))
        scan_btn = QPushButton(tr("Scan"))
        scan_btn.setToolTip(tr("Auto-detect Calibre library locations"))
        scan_btn.clicked.connect(self._auto_detect_calibre)
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse_calibre)
        lib_row.addWidget(self.calibre_edit, 1)
        lib_row.addWidget(scan_btn)
        lib_row.addWidget(browse)
        lib_row_w = QWidget()
        lib_row_w.setLayout(lib_row)
        misc_form.addRow(tr("Calibre Library:"), lib_row_w)

        xray_out_row = QHBoxLayout()
        self.xray_output_edit = QLineEdit(os.environ.get("XRAY_OUTPUT_DIR", ""))
        self.xray_output_edit.setPlaceholderText(
            tr("Default: <app folder>/xray")
        )
        xray_browse = QPushButton(tr("Browse…"))
        xray_browse.clicked.connect(self._browse_xray_output)
        xray_out_row.addWidget(self.xray_output_edit, 1)
        xray_out_row.addWidget(xray_browse)
        xray_out_row_w = QWidget()
        xray_out_row_w.setLayout(xray_out_row)
        misc_form.addRow(tr("X-Ray Output Folder:"), xray_out_row_w)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(float(getattr(generator, "TEMPERATURE", 0.4)))
        misc_form.addRow(tr("Temperature:"), self.temp_spin)

        self.lang_combo = QComboBox()
        for code, name in AVAILABLE_LANGUAGES:
            self.lang_combo.addItem(name, code)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        misc_form.addRow(tr("Language:"), self.lang_combo)
        layout.addWidget(misc_box)
        layout.addStretch(1)

        # Wrap the settings stack in a scroll area so the tab stays usable on
        # short displays (the settings stack is taller than some screens).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(w)

        # Action bar: pinned below the scroll area so Apply / Load / Save are
        # always visible no matter how far the settings are scrolled.
        action_bar = QWidget()
        action_bar.setObjectName("configActionBar")
        action_bar.setStyleSheet(
            "#configActionBar { background: palette(window); "
            "border-top: 1px solid palette(mid); }"
        )
        btn_row = QHBoxLayout(action_bar)
        btn_row.setContentsMargins(8, 8, 8, 8)
        apply_btn = QPushButton(tr("Apply Settings"))
        apply_btn.setDefault(True)
        apply_btn.setStyleSheet("font-weight: bold; padding: 6px 18px;")
        apply_btn.clicked.connect(self._apply_config)
        load_env_btn = QPushButton(tr("Load .env"))
        load_env_btn.clicked.connect(self._load_env)
        save_env_btn = QPushButton(tr("Save .env"))
        save_env_btn.clicked.connect(self._save_env)
        btn_row.addStretch(1)
        btn_row.addWidget(load_env_btn)
        btn_row.addWidget(save_env_btn)
        btn_row.addWidget(apply_btn)

        # Container = scrollable settings on top, fixed action bar on the bottom.
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addWidget(action_bar)
        return container

    # ------------------------------------------------------------- books tab
    def _build_books_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        self.scan_btn = QPushButton(tr("Scan Library"))
        self.scan_btn.clicked.connect(self._scan_library)
        add_btn = QPushButton(tr("Add EPUB…"))
        add_btn.clicked.connect(self._add_epub)
        cleanup_btn = QPushButton(tr("Cleanup Ghost Folders"))
        cleanup_btn.clicked.connect(self._cleanup_ghosts)
        refresh_all_btn = QPushButton(tr("Refresh All"))
        refresh_all_btn.clicked.connect(self._populate_table)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("Filter by title or author…"))
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.scan_btn)
        top.addWidget(add_btn)
        top.addWidget(cleanup_btn)
        top.addWidget(refresh_all_btn)
        top.addWidget(self.filter_edit, 1)
        layout.addLayout(top)

        self.book_table = QTableWidget(0, 5)
        self.book_table.setHorizontalHeaderLabels(
            [tr("Title"), tr("Author"), tr("Added"), tr("Status"), tr("WebDAV")]
        )
        self.book_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.book_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.book_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.book_table.itemSelectionChanged.connect(self._update_push_button_label)
        header = self.book_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.book_table, 1)

        bottom = QHBoxLayout()
        select_all = QPushButton(tr("Select All"))
        select_all.clicked.connect(self.book_table.selectAll)
        self.book_count_label = QLabel(tr("{n} books").format(n=0))
        self.open_folder_btn = QPushButton(tr("Open Local Folder"))
        self.open_folder_btn.clicked.connect(self._open_local_folder)
        self.delete_xray_btn = QPushButton(tr("Delete Local X-Ray"))
        self.delete_xray_btn.clicked.connect(self._delete_local_xray)
        self.webdav_upload_btn = QPushButton(tr("Upload Selected to WebDAV"))
        self.webdav_upload_btn.clicked.connect(self._webdav_upload_selected)
        self.webdav_download_btn = QPushButton(tr("Download Selected from WebDAV"))
        self.webdav_download_btn.clicked.connect(self._webdav_download_selected)
        self.webdav_delete_btn = QPushButton(tr("Delete Selected from WebDAV"))
        self.webdav_delete_btn.clicked.connect(self._webdav_delete_selected)
        self.webdav_refresh_btn = QPushButton(tr("Refresh Selected"))
        self.webdav_refresh_btn.clicked.connect(self._refresh_selected_webdav_status)
        self.start_btn = QPushButton(tr("Generate X-Ray"))
        self.start_btn.clicked.connect(self._start_analysis)
        self._chain_info_label = _ChainInfoLabel(lambda: self._chain_tooltip_text())
        bottom.addWidget(select_all)
        bottom.addWidget(self.book_count_label)
        bottom.addWidget(self.open_folder_btn)
        bottom.addWidget(self.delete_xray_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.webdav_upload_btn)
        bottom.addWidget(self.webdav_download_btn)
        bottom.addWidget(self.webdav_delete_btn)
        bottom.addWidget(self.webdav_refresh_btn)
        _start_w = QWidget()
        _start_l = QHBoxLayout(_start_w)
        _start_l.setContentsMargins(0, 0, 0, 0)
        _start_l.setSpacing(4)
        _start_l.addWidget(self.start_btn)
        _start_l.addWidget(self._chain_info_label)
        bottom.addWidget(_start_w)
        layout.addLayout(bottom)
        return w

    # ---------------------------------------------------------- progress tab
    def _build_progress_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        overall_box = QGroupBox(tr("Overall Progress"))
        ov = QFormLayout(overall_box)
        self.overall_bar = QProgressBar()
        self.overall_label = QLabel(tr("Idle"))
        ov.addRow(tr("Batch:"), self.overall_bar)
        ov.addRow("", self.overall_label)
        # Stop button lives with the live progress it controls.
        stop_row = QHBoxLayout()
        stop_row.addStretch(1)
        self.stop_btn = QPushButton(tr("Stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_analysis)
        stop_row.addWidget(self.stop_btn)
        ov.addRow("", stop_row)
        layout.addWidget(overall_box)

        book_box = QGroupBox(tr("Current Book"))
        bk = QFormLayout(book_box)
        self.book_bar = QProgressBar()
        self.current_book_label = QLabel("—")
        self.chunk_label = QLabel("—")
        self.op_label = QLabel("—")
        bk.addRow(tr("Book:"), self.current_book_label)
        bk.addRow(tr("Progress:"), self.book_bar)
        bk.addRow(tr("Chunk:"), self.chunk_label)
        bk.addRow(tr("Operation:"), self.op_label)
        layout.addWidget(book_box)

        stats_box = QGroupBox(tr("Stats"))
        st = QHBoxLayout(stats_box)
        self.stat_chars = QLabel(tr("Characters: {n}").format(n=0))
        self.stat_locs = QLabel(tr("Locations: {n}").format(n=0))
        self.stat_events = QLabel(tr("Events: {n}").format(n=0))
        for lbl in (self.stat_chars, self.stat_locs, self.stat_events):
            st.addWidget(lbl)
        st.addStretch(1)
        layout.addWidget(stats_box)

        log_box = QGroupBox(tr("Log"))
        lg = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFont("Consolas", 9))
        lg.addWidget(self.log_view, 1)
        log_btns = QHBoxLayout()
        clear_btn = QPushButton(tr("Clear"))
        clear_btn.clicked.connect(self.log_view.clear)
        save_btn = QPushButton(tr("Save Log…"))
        save_btn.clicked.connect(self._save_log)
        self.autoscroll_chk = QCheckBox(tr("Auto-scroll"))
        self.autoscroll_chk.setChecked(True)
        log_btns.addWidget(clear_btn)
        log_btns.addWidget(save_btn)
        log_btns.addWidget(self.autoscroll_chk)
        log_btns.addStretch(1)
        lg.addLayout(log_btns)
        layout.addWidget(log_box, 1)
        return w

    # -------------------------------------------------------------- sync tab
    def _build_sync_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        box = QGroupBox(tr("KOReader Device"))
        form = QFormLayout(box)
        self.device_edit = QLineEdit()
        self.device_edit.setPlaceholderText("192.168.1.42  or  192.168.1.42:8763")
        test_btn = QPushButton(tr("Test Connection"))
        test_btn.clicked.connect(self._test_device)
        row = QHBoxLayout()
        row.addWidget(self.device_edit, 1)
        row.addWidget(test_btn)
        rw = QWidget()
        rw.setLayout(row)
        form.addRow(tr("Device IP[:port]:"), rw)

        self.autopush_chk = QCheckBox(tr("Push results automatically after each book"))
        form.addRow("", self.autopush_chk)

        self.push_now_btn = QPushButton(tr("Push Selected Book Now"))
        self.push_now_btn.clicked.connect(self._push_selected)
        form.addRow("", self.push_now_btn)

        self.device_status = QLabel(
            tr("On KOReader: X-Ray menu → Cloud Sync → Receive from PC")
        )
        self.device_status.setWordWrap(True)
        form.addRow(tr("Status:"), self.device_status)
        layout.addWidget(box)

        # ------------------------------------------------------------ WebDAV
        wd_box = QGroupBox(tr("WebDAV Cloud Sync"))
        wd = QVBoxLayout(wd_box)

        wd_form = QFormLayout()
        self.webdav_url_edit = QLineEdit()
        self.webdav_url_edit.setPlaceholderText(
            "https://dav.example.com/remote.php/dav/files/USER/koreader/xray"
        )
        wd_form.addRow(tr("Server URL:"), self.webdav_url_edit)

        self.webdav_user_edit = QLineEdit()
        wd_form.addRow(tr("Username:"), self.webdav_user_edit)

        self.webdav_pass_edit = QLineEdit()
        self.webdav_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.webdav_pass_edit, 1)
        pass_show = QCheckBox(tr("Show"))
        pass_show.stateChanged.connect(
            lambda state: self.webdav_pass_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if state else QLineEdit.EchoMode.Password
            )
        )
        pass_row.addWidget(pass_show)
        pass_row_w = QWidget()
        pass_row_w.setLayout(pass_row)
        wd_form.addRow(tr("Password:"), pass_row_w)

        self.webdav_status_label = QLabel(tr("Not configured."))
        self.webdav_status_label.setWordWrap(True)
        wd_form.addRow(tr("Status:"), self.webdav_status_label)

        self.webdav_url_edit.textChanged.connect(self._on_webdav_credentials_edited)
        self.webdav_user_edit.textChanged.connect(self._on_webdav_credentials_edited)
        self.webdav_pass_edit.textChanged.connect(self._on_webdav_credentials_edited)
        self._on_webdav_credentials_edited()
        wd.addLayout(wd_form)

        hint = QLabel(
            tr(
                "Point KOReader and this app at the same WebDAV folder so "
                "X-Ray data syncs both ways."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        wd.addWidget(hint)

        wd_btns = QHBoxLayout()
        wd_test_btn = QPushButton(tr("Test Connection"))
        wd_test_btn.clicked.connect(self._test_webdav)
        wd_btns.addWidget(wd_test_btn)
        self.webdav_browse_btn = QPushButton(tr("Choose Path"))
        self.webdav_browse_btn.clicked.connect(self._browse_webdav_folder)
        wd_btns.addWidget(self.webdav_browse_btn)
        wd_btns.addStretch(1)
        wd.addLayout(wd_btns)

        self.webdav_autopush_chk = QCheckBox(
            tr("Upload to WebDAV automatically after each book")
        )
        wd.addWidget(self.webdav_autopush_chk)
        layout.addWidget(wd_box)

        layout.addStretch(1)
        return w

    # ----------------------------------------------------------- results tab
    def _build_results_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        load_btn = QPushButton(tr("Open xray_data.json…"))
        load_btn.clicked.connect(self._open_result_file)
        load_sel_btn = QPushButton(tr("Load Selected Book's Result"))
        load_sel_btn.clicked.connect(self._load_selected_result)
        top.addWidget(load_btn)
        top.addWidget(load_sel_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self.result_title = QLabel(tr("No result loaded."))
        self.result_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.result_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels([tr("Entity"), tr("Detail")])
        self.result_tree.itemClicked.connect(self._on_result_item)
        splitter.addWidget(self.result_tree)
        self.result_detail = QTextEdit()
        self.result_detail.setReadOnly(True)
        splitter.addWidget(self.result_detail)
        splitter.setSizes([420, 620])
        layout.addWidget(splitter, 1)
        return w

    # =========================================== retry / fallback chain editor
    _CHAIN_COLS = ("Provider", "Model", "Retries", "Cooldown (s)", "Input ($/M tok)", "Output ($/M tok)")

    def _build_chain_box(self) -> QGroupBox:
        box = QGroupBox(tr("Retry / Fallback Chain"))
        v = QVBoxLayout(box)

        hint = QLabel(
            tr(
                "Models are tried top-to-bottom. Each is retried up to its "
                "retry count with the given cooldown between requests. A "
                "content-moderation refusal skips straight to the next row."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        v.addWidget(hint)

        self.chain_table = QTableWidget(0, len(self._CHAIN_COLS))
        self.chain_table.setIconSize(QSize(_PROVIDER_ICON_SIZE, _PROVIDER_ICON_SIZE))
        self.chain_table.setHorizontalHeaderLabels([tr(c) for c in self._CHAIN_COLS])
        self.chain_table.verticalHeader().setVisible(False)
        self.chain_table.verticalHeader().setDefaultSectionSize(provider_row_height())
        self.chain_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.chain_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        hh = self.chain_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.chain_table.setMinimumHeight(150)
        v.addWidget(self.chain_table)

        self._start_chain_price_fetch()

        # Row controls.
        row_btns = QHBoxLayout()
        add_btn = QPushButton(tr("Add Model"))
        add_btn.clicked.connect(self._open_add_model_dialog)
        add_blank_btn = QPushButton(tr("Add row"))
        add_blank_btn.clicked.connect(lambda: self._chain_add_row())
        rm_btn = QPushButton(tr("Remove"))
        rm_btn.clicked.connect(self._chain_remove_selected)
        up_btn = QPushButton(tr("↑"))
        up_btn.clicked.connect(lambda: self._chain_move(-1))
        down_btn = QPushButton(tr("↓"))
        down_btn.clicked.connect(lambda: self._chain_move(1))
        for b in (add_btn, add_blank_btn, rm_btn, up_btn, down_btn):
            row_btns.addWidget(b)
        row_btns.addStretch(1)
        v.addLayout(row_btns)

        # Chain-level options.
        opts_form = QFormLayout()
        self.chain_max_cycles = QSpinBox()
        self.chain_max_cycles.setRange(1, 100)
        self.chain_max_cycles.setValue(retry_config.DEFAULT_MAX_CYCLES)
        opts_form.addRow(tr("Max full-chain cycles:"), self.chain_max_cycles)

        self.chain_inter_wait = QDoubleSpinBox()
        self.chain_inter_wait.setRange(0.0, 3600.0)
        self.chain_inter_wait.setSingleStep(1.0)
        self.chain_inter_wait.setValue(retry_config.DEFAULT_INTER_CYCLE_WAIT)
        opts_form.addRow(tr("Wait between cycles (s):"), self.chain_inter_wait)

        self.chain_on_exhausted = QComboBox()
        for key, label in (
            ("raise", tr("Raise error (skip this book)")),
            ("skip", tr("Skip request (empty result)")),
            ("exit", tr("Exit the program")),
        ):
            self.chain_on_exhausted.addItem(label, key)
        opts_form.addRow(tr("When chain is exhausted:"), self.chain_on_exhausted)

        self.chain_honor_retry_after = QCheckBox(
            tr("Honor server Retry-After header on rate limits")
        )
        self.chain_honor_retry_after.setChecked(True)
        opts_form.addRow("", self.chain_honor_retry_after)
        v.addLayout(opts_form)

        return box

    def _chain_add_row(
        self,
        provider: str = "openai",
        model: str = "",
        retries: int = retry_config.DEFAULT_RETRIES,
        cooldown: float = retry_config.DEFAULT_COOLDOWN,
    ) -> None:
        table = self.chain_table
        r = table.rowCount()
        table.insertRow(r)

        prov_combo = QComboBox()
        populate_provider_combo(prov_combo)
        idx = prov_combo.findData(provider)
        if idx >= 0:
            prov_combo.setCurrentIndex(idx)
        prov_combo.currentIndexChanged.connect(lambda _, row=r: self._chain_on_provider_changed(row))
        table.setCellWidget(r, 0, prov_combo)

        model_combo = QComboBox()
        model_combo.setEditable(True)
        model_combo.addItems(fetch_models_for(provider))
        model_combo.setCurrentText(model)
        model_combo.currentTextChanged.connect(lambda _, row=r: self._chain_refresh_cost_row(row))
        table.setCellWidget(r, 1, model_combo)

        retries_spin = QSpinBox()
        retries_spin.setRange(1, 50)
        retries_spin.setValue(max(1, int(retries)))
        table.setCellWidget(r, 2, retries_spin)

        cooldown_spin = QDoubleSpinBox()
        cooldown_spin.setRange(0.0, 3600.0)
        cooldown_spin.setSingleStep(0.5)
        cooldown_spin.setValue(max(0.0, float(cooldown)))
        table.setCellWidget(r, 3, cooldown_spin)

        inp_item = QTableWidgetItem("…" if not self._price_catalog else "")
        inp_item.setFlags(inp_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        out_item = QTableWidgetItem("…" if not self._price_catalog else "")
        out_item.setFlags(out_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(r, 4, inp_item)
        table.setItem(r, 5, out_item)
        if self._price_catalog:
            self._chain_fill_cost_row(r, provider, model)

    def _start_chain_price_fetch(self) -> None:
        """Fetch LiteLLM prices in the background and populate cost columns."""
        if self._price_thread and self._price_thread.isRunning():
            return  # already in progress
        self._price_thread = QThread()
        self._price_fetcher = _PriceFetcher()
        self._price_fetcher.moveToThread(self._price_thread)
        self._price_thread.started.connect(self._price_fetcher.run)
        self._price_fetcher.done.connect(self._on_chain_prices_loaded)
        self._price_fetcher.done.connect(self._price_thread.quit)
        self._price_thread.finished.connect(self._cleanup_price_thread)
        self._price_thread.start()

    def _cleanup_price_thread(self) -> None:
        if self._price_thread is not None:
            self._price_thread.wait()
        self._price_thread = None
        self._price_fetcher = None

    def _on_chain_prices_loaded(self, catalog: dict, _last_modified: str) -> None:
        self._price_catalog = catalog
        self._chain_refresh_costs()

    def _chain_fill_cost_row(self, r: int, provider: str, model: str) -> None:
        price = _lookup_litellm_price(self._price_catalog, provider, model)
        table = self.chain_table
        inp_item = table.item(r, 4)
        out_item = table.item(r, 5)
        if inp_item is None or out_item is None:
            return  # row not fully constructed yet
        if price:
            inp_per_m = price[0] * 1_000_000
            out_per_m = price[1] * 1_000_000
            table.item(r, 4).setText(f"${inp_per_m:.4f}")  # noqa: i18n
            table.item(r, 5).setText(f"${out_per_m:.4f}")  # noqa: i18n
        else:
            table.item(r, 4).setText("—")
            table.item(r, 5).setText("—")

    def _chain_refresh_costs(self) -> None:
        """Update cost columns for every row from the cached price catalog."""
        table = self.chain_table
        for r in range(table.rowCount()):
            self._chain_refresh_cost_row(r)

    def _chain_refresh_cost_row(self, r: int) -> None:
        table = self.chain_table
        if r < 0 or r >= table.rowCount():
            return
        prov_widget = table.cellWidget(r, 0)
        provider = prov_widget.currentData() if prov_widget else "openai"
        mdl_widget = table.cellWidget(r, 1)
        model = mdl_widget.currentText().strip() if mdl_widget else ""
        self._chain_fill_cost_row(r, provider, model)

    def _chain_on_provider_changed(self, r: int) -> None:
        """Repopulate the model combo for row r and refresh its cost."""
        table = self.chain_table
        if r < 0 or r >= table.rowCount():
            return
        prov_widget = table.cellWidget(r, 0)
        provider = prov_widget.currentData() if prov_widget else "openai"
        mdl_widget = table.cellWidget(r, 1)
        if mdl_widget:
            current = mdl_widget.currentText()
            mdl_widget.blockSignals(True)
            mdl_widget.clear()
            mdl_widget.addItems(fetch_models_for(provider))
            mdl_widget.setCurrentText(current)
            mdl_widget.blockSignals(False)
        self._chain_refresh_cost_row(r)

    def _open_add_model_dialog(self) -> None:
        """Open the provider/model picker popup and add the result to the chain."""
        dlg = _AddModelDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            provider = dlg.selected_provider()
            model = dlg.selected_model()
            # Save API key entered in the dialog back to the settings fields.
            env_var = _PROVIDER_KEY_MAPPING.get(provider, "")
            key_text = dlg.api_key_edit.text().strip()
            if env_var and key_text and env_var in self._key_edits:
                self._key_edits[env_var].setText(key_text)
            # Sync hidden combos so prefs/env-save/start-analysis fallback stays current.
            idx = self.provider_combo.findData(provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.model_combo.setEditText(model)
            self._chain_add_row(provider=provider, model=model)
            self.chain_table.selectRow(self.chain_table.rowCount() - 1)

    def _chain_add_current(self) -> None:
        provider = self.provider_combo.currentData() or "openai"
        model = self.model_combo.currentText().strip()
        self._chain_add_row(provider=provider, model=model)
        self.chain_table.selectRow(self.chain_table.rowCount() - 1)

    def _chain_remove_selected(self) -> None:
        r = self.chain_table.currentRow()
        if r >= 0:
            self.chain_table.removeRow(r)

    def _chain_move(self, delta: int) -> None:
        table = self.chain_table
        r = table.currentRow()
        n = table.rowCount()
        if r < 0:
            return
        target = r + delta
        if not (0 <= target < n):
            return
        rows = self._chain_rows()
        rows[r], rows[target] = rows[target], rows[r]
        self._populate_chain_table(
            RetryChain(entries=rows, options=self._chain_options_from_ui())
        )
        table.selectRow(target)

    def _chain_rows(self) -> list[RetryEntry]:
        """Read the table into a list of RetryEntry (unfiltered)."""
        entries: list[RetryEntry] = []
        table = self.chain_table
        for r in range(table.rowCount()):
            prov_widget = table.cellWidget(r, 0)
            provider = prov_widget.currentData() if prov_widget else "openai"
            mdl_widget = table.cellWidget(r, 1)
            model = mdl_widget.currentText().strip() if mdl_widget else ""
            retries_widget = table.cellWidget(r, 2)
            retries = retries_widget.value() if retries_widget else retry_config.DEFAULT_RETRIES
            cooldown_widget = table.cellWidget(r, 3)
            cooldown = cooldown_widget.value() if cooldown_widget else retry_config.DEFAULT_COOLDOWN
            entries.append(
                RetryEntry(
                    provider=provider,
                    model=model,
                    retries=int(retries),
                    cooldown=float(cooldown),
                )
            )
        return entries

    def _chain_options_from_ui(self) -> RetryChainOptions:
        return RetryChainOptions(
            max_cycles=int(self.chain_max_cycles.value()),
            inter_cycle_wait=float(self.chain_inter_wait.value()),
            on_exhausted=self.chain_on_exhausted.currentData() or "raise",
            honor_retry_after=self.chain_honor_retry_after.isChecked(),
        )

    def _chain_from_ui(self) -> RetryChain:
        """Build a validated RetryChain from the editor (drops empty rows)."""
        entries = [e for e in self._chain_rows() if e.is_valid()]
        return RetryChain(entries=entries, options=self._chain_options_from_ui())

    def _chain_tooltip_text(self) -> str:
        """Return a plain-text summary of the current model chain for the info tooltip."""
        chain = self._chain_from_ui()
        if not chain.entries:
            return tr("No models configured in the chain.")
        lines = [tr("Model chain:")]
        for i, e in enumerate(chain.entries, 1):
            lines.append(
                tr("  {i}. {provider} / {model}  (\u00d7{retries}, {cooldown:.0f}s cooldown)").format(
                    i=i, provider=e.provider, model=e.model,
                    retries=e.retries, cooldown=e.cooldown,
                )
            )
        opts = chain.options
        lines.append("")
        lines.append(tr("Max cycles: {n}").format(n=opts.max_cycles))
        if opts.inter_cycle_wait > 0:
            lines.append(
                tr("Wait between cycles: {n:.0f}s").format(n=opts.inter_cycle_wait)
            )
        return "\n".join(lines)

    def _populate_chain_table(self, chain: RetryChain) -> None:
        self.chain_table.setRowCount(0)
        for e in chain.entries:
            self._chain_add_row(
                provider=e.provider,
                model=e.model,
                retries=e.retries,
                cooldown=e.cooldown,
            )
        opts = chain.options
        self.chain_max_cycles.setValue(opts.max_cycles)
        self.chain_inter_wait.setValue(opts.inter_cycle_wait)
        idx = self.chain_on_exhausted.findData(opts.on_exhausted)
        if idx >= 0:
            self.chain_on_exhausted.setCurrentIndex(idx)
        self.chain_honor_retry_after.setChecked(opts.honor_retry_after)

    # ================================================ performance / limits editor
    _LIMIT_COLS = ("Provider", "Max Workers", "Max Chunk Size (chars)")

    def _build_limits_box(self) -> QGroupBox:
        box = QGroupBox(tr("Concurrency & Chunk-Size Limits"))
        v = QVBoxLayout(box)

        hint = QLabel(
            tr(
                "Per-provider limits. Max Workers = parallel chunk requests. "
                "Max Chunk Size = characters sent per request. Set either to 0 "
                "(auto) to use the built-in default (Groq auto-derives chunk "
                "size from its token-per-minute budget)."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        v.addWidget(hint)

        self.limits_table = QTableWidget(len(PROVIDERS), len(self._LIMIT_COLS))
        self.limits_table.setIconSize(QSize(_PROVIDER_ICON_SIZE, _PROVIDER_ICON_SIZE))
        self.limits_table.setHorizontalHeaderLabels([tr(c) for c in self._LIMIT_COLS])
        self.limits_table.verticalHeader().setVisible(False)
        self.limits_table.verticalHeader().setDefaultSectionSize(provider_row_height())
        self.limits_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        hh = self.limits_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self._limit_worker_spins: dict[str, QSpinBox] = {}
        self._limit_chunk_spins: dict[str, QSpinBox] = {}
        for row, (key, label) in enumerate(PROVIDERS):
            name_item = provider_table_item(key)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            self.limits_table.setItem(row, 0, name_item)

            worker_spin = QSpinBox()
            worker_spin.setRange(0, 64)
            # value 0 = "auto": show the actual default that will be used instead
            # of a generic "auto" label.
            worker_spin.setSpecialValueText(str(ai_client.auto_max_workers(key)))
            worker_spin.setValue(0)
            self.limits_table.setCellWidget(row, 1, worker_spin)
            self._limit_worker_spins[key] = worker_spin

            chunk_spin = QSpinBox()
            chunk_spin.setRange(0, 200_000)
            chunk_spin.setSingleStep(1000)
            # value 0 = "auto": show the actual chunk size that will be used.
            # Groq derives it from the (unknown-model) TPM budget; others use the
            # global default.
            chunk_spin.setSpecialValueText(
                str(ai_client.auto_max_chunk_size(key, "__auto__" if key == "groq" else None))
            )
            chunk_spin.setValue(0)
            self.limits_table.setCellWidget(row, 2, chunk_spin)
            self._limit_chunk_spins[key] = chunk_spin

        vh = self.limits_table.verticalHeader().defaultSectionSize()
        self.limits_table.setMinimumHeight(vh * (len(PROVIDERS) + 1) + 30)
        v.addWidget(self.limits_table)

        # Global consolidation batch size (entities merged per merge request).
        consol_row = QHBoxLayout()
        consol_label = QLabel(tr("Consolidation Batch Size:"))
        self.consolidation_spin = QSpinBox()
        self.consolidation_spin.setRange(0, 200)
        # value 0 = "auto": show the built-in default that will be used.
        self.consolidation_spin.setSpecialValueText(
            str(ai_client.CONSOLIDATE_BATCH_SIZE_DEFAULT)
        )
        self.consolidation_spin.setValue(0)
        self.consolidation_spin.setToolTip(
            tr(
                "Number of entities (characters + locations + summary) merged "
                "into one consolidation request. Higher = fewer requests but "
                "larger payloads. 0 (auto) uses the built-in default."
            )
        )
        consol_row.addWidget(consol_label)
        consol_row.addWidget(self.consolidation_spin)
        consol_row.addStretch(1)
        v.addLayout(consol_row)
        return box

    def _limits_from_ui(self) -> tuple[dict[str, int], dict[str, int]]:
        """Return (max_workers, max_chunk_size) dicts, omitting 0 (=auto)."""
        workers = {
            key: spin.value()
            for key, spin in self._limit_worker_spins.items()
            if spin.value() > 0
        }
        chunk = {
            key: spin.value()
            for key, spin in self._limit_chunk_spins.items()
            if spin.value() > 0
        }
        return workers, chunk

    def _populate_limits_table(
        self, workers: dict[str, Any], chunk: dict[str, Any]
    ) -> None:
        for key, spin in self._limit_worker_spins.items():
            try:
                spin.setValue(int(workers.get(key, 0)))
            except (TypeError, ValueError):
                spin.setValue(0)
        for key, spin in self._limit_chunk_spins.items():
            try:
                spin.setValue(int(chunk.get(key, 0)))
            except (TypeError, ValueError):
                spin.setValue(0)

    # ============================================================ prefs / env
    def _load_prefs_into_ui(self) -> None:
        import gui_i18n
        idx = self.lang_combo.findData(gui_i18n.get_language())
        if idx >= 0:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(idx)
            self.lang_combo.blockSignals(False)
        api = self._prefs.get("last_api", "openai")
        idx = self.provider_combo.findData(api)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        model = self._prefs.get("last_model", "")
        if model:
            self.model_combo.setEditText(model)
        self.device_edit.setText(self._prefs.get("device", ""))
        self.autopush_chk.setChecked(bool(self._prefs.get("auto_push", False)))
        self.webdav_url_edit.setText(self._prefs.get("webdav_url", ""))
        self.webdav_user_edit.setText(self._prefs.get("webdav_user", ""))
        self.webdav_pass_edit.setText(self._prefs.get("webdav_pass", ""))
        self.webdav_autopush_chk.setChecked(
            bool(self._prefs.get("webdav_auto_push", False))
        )
        if self._prefs.get("calibre_library"):
            self.calibre_edit.setText(self._prefs["calibre_library"])
        if self._prefs.get("xray_output_dir"):
            self.xray_output_edit.setText(self._prefs["xray_output_dir"])
        if "temperature" in self._prefs:
            self.temp_spin.setValue(float(self._prefs["temperature"]))
        # Populate the retry/fallback chain editor from prefs (or legacy keys).
        self._populate_chain_table(retry_config.load_retry_chain())
        # Populate the per-provider concurrency / chunk-size limits.
        self._populate_limits_table(
            self._prefs.get("max_workers") or {},
            self._prefs.get("max_chunk_size") or {},
        )
        try:
            self.consolidation_spin.setValue(
                int(self._prefs.get("consolidation_batch_size", 0))
            )
        except (TypeError, ValueError):
            self.consolidation_spin.setValue(0)

    def _open_setup_wizard(self) -> None:
        if not self._setup_wizard_has_launched():
            self._prefs["setup_wizard_launched"] = True
            self._prefs["setup_wizard_seen"] = True
            calibre_browser._save_preferences(self._prefs)
            self._refresh_setup_wizard_menu_action()
        dlg = SetupWizard(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to_parent()
            self.statusBar().showMessage(tr("Setup wizard completed."), 5000)

    def _setup_wizard_has_launched(self) -> bool:
        return bool(self._prefs.get("setup_wizard_launched", False))

    def _refresh_setup_wizard_menu_action(self) -> None:
        if self._wizard_menu_action is None:
            return
        base = tr("Setup Wizard")
        is_new = not self._setup_wizard_has_launched()
        self._wizard_menu_action.setText((base + " ★") if is_new else base)
        font = self._wizard_menu_action.font()
        font.setBold(is_new)
        self._wizard_menu_action.setFont(font)

    def _maybe_offer_setup_wizard(self) -> None:
        if self._setup_wizard_has_launched():
            return
        has_calibre = bool(self.calibre_edit.text().strip())
        has_key = any(
            bool(self._key_edits.get(var) and self._key_edits[var].text().strip())
            for var in _PROVIDER_KEY_MAPPING.values()
        )
        if has_calibre and has_key:
            return
        answer = QMessageBox.question(
            self,
            tr("Run setup wizard?"),
            tr("Would you like a guided setup for provider, library, and sync settings?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._open_setup_wizard()

    def _save_prefs(self) -> None:
        chain = self._chain_from_ui()
        primary = chain.primary
        workers, chunk = self._limits_from_ui()
        self._prefs.update(
            {
                "last_api": primary.provider if primary else self.provider_combo.currentData(),
                "last_model": primary.model if primary else self.model_combo.currentText().strip(),
                "device": self.device_edit.text().strip(),
                "auto_push": self.autopush_chk.isChecked(),
                "webdav_url": self.webdav_url_edit.text().strip(),
                "webdav_user": self.webdav_user_edit.text().strip(),
                "webdav_pass": self.webdav_pass_edit.text(),
                "webdav_auto_push": self.webdav_autopush_chk.isChecked(),
                "calibre_library": self.calibre_edit.text().strip(),
                "xray_output_dir": self.xray_output_edit.text().strip(),
                "temperature": self.temp_spin.value(),
                "gui_lang": self.lang_combo.currentData(),
                "retry_chain": [e.to_dict() for e in chain.entries],
                "retry_chain_options": chain.options.to_dict(),
                "max_workers": workers,
                "max_chunk_size": chunk,
                "consolidation_batch_size": self.consolidation_spin.value(),
            }
        )
        calibre_browser._save_preferences(self._prefs)

    def _on_language_changed(self) -> None:
        """Persist the chosen language; a restart applies it everywhere."""
        self._prefs["gui_lang"] = self.lang_combo.currentData()
        calibre_browser._save_preferences(self._prefs)
        self.statusBar().showMessage(
            tr("Language changed. Restart the app to apply."), 6000
        )

    def _apply_config(self) -> None:
        """Push the UI's key/endpoint values into the backend modules."""
        for env_var, attr, _label, _secret in KEY_FIELDS:
            val = self._key_edits[env_var].text().strip()
            os.environ[env_var] = val
            if attr:
                setattr(ai_client, attr, val)
        # OpenAI-compatible custom headers (parsed from JSON or Key: Value lines).
        raw_headers = self._headers_edit.toPlainText().strip()
        os.environ["XRAY_API_HEADERS"] = raw_headers
        ai_client.API_DEFAULT_HEADERS = ai_client.parse_headers(raw_headers)
        lib = self.calibre_edit.text().strip()
        os.environ["CALIBRE_LIBRARY"] = lib
        generator.CALIBRE_LIBRARY = lib
        xray_out = self.xray_output_edit.text().strip()
        os.environ["XRAY_OUTPUT_DIR"] = xray_out
        temp = float(self.temp_spin.value())
        ai_client.TEMPERATURE = temp
        generator.TEMPERATURE = temp
        # Push per-provider concurrency / chunk-size overrides into the backend.
        workers, chunk = self._limits_from_ui()
        ai_client.configure_performance(
            max_workers=workers,
            max_chunk_size=chunk,
            consolidate_batch_size=self.consolidation_spin.value(),
        )
        self._save_prefs()
        self.statusBar().showMessage(tr("Settings applied."), 4000)

    def _load_env(self) -> None:
        path = os.path.join(_SCRIPT_DIR, ".env")
        if not os.path.exists(path):
            QMessageBox.information(
                self, tr("No .env"),
                tr("No .env file found at:\n{path}").format(path=path),
            )
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ[key] = value
                    if key in self._key_edits:
                        self._key_edits[key].setText(value)
                    elif key == "XRAY_API_HEADERS":
                        self._headers_edit.setPlainText(value)
                    elif key == "CALIBRE_LIBRARY":
                        self.calibre_edit.setText(value)
                    elif key == "XRAY_OUTPUT_DIR":
                        self.xray_output_edit.setText(value)
        except OSError as e:
            QMessageBox.warning(self, tr("Load .env failed"), str(e))
            return
        self.statusBar().showMessage(tr("Loaded settings from .env"), 4000)

    def _save_env(self) -> None:
        path = os.path.join(_SCRIPT_DIR, ".env")
        lines = ["# X-Ray Generator Configuration (written by GUI)"]
        for env_var, _attr, _label, _secret in KEY_FIELDS:
            lines.append(f"{env_var}={self._key_edits[env_var].text().strip()}")
        raw_headers = self._headers_edit.toPlainText().strip()
        if raw_headers:
            lines.append(
                f"XRAY_API_HEADERS={json.dumps(ai_client.parse_headers(raw_headers))}"
            )
        lines.append(f"CALIBRE_LIBRARY={self.calibre_edit.text().strip()}")
        xray_out = self.xray_output_edit.text().strip()
        if xray_out:
            lines.append(f"XRAY_OUTPUT_DIR={xray_out}")
        model = self.model_combo.currentText().strip()
        if model:
            lines.append(f"XRAY_MODEL={model}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            QMessageBox.warning(self, tr("Save .env failed"), str(e))
            return
        self.statusBar().showMessage(
            tr("Saved settings to {path}").format(path=path), 5000
        )

    def _browse_calibre(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("Select Calibre Library"), self.calibre_edit.text() or _SCRIPT_DIR
        )
        if d:
            self.calibre_edit.setText(d)

    def _browse_xray_output(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("Select X-Ray Output Folder"),
            self.xray_output_edit.text() or generator.get_xray_base_dir(),
        )
        if d:
            self.xray_output_edit.setText(d)

    def _auto_detect_calibre(self) -> None:
        """Scan common locations for Calibre libraries and let the user pick one."""
        found = calibre_browser.find_calibre_libraries()
        if not found:
            QMessageBox.information(
                self,
                tr("No Calibre Library Found"),
                tr("Could not find a Calibre library in common locations.\n"
                   "Use Browse… to select it manually."),
            )
            return
        if len(found) == 1:
            self.calibre_edit.setText(found[0])
            self.statusBar().showMessage(
                tr("Calibre library auto-detected: {path}").format(path=found[0]), 5000
            )
            return
        # Multiple found — let the user choose.
        item, ok = QInputDialog.getItem(
            self,
            tr("Select Calibre Library"),
            tr("Multiple Calibre libraries found. Select one:"),
            found,
            0,
            False,
        )
        if ok and item:
            self.calibre_edit.setText(item)

    # =========================================================== provider/model
    def _on_provider_changed(self) -> None:
        api = self.provider_combo.currentData()
        key_ok = self._provider_key_present(api)
        if key_ok:
            self.provider_hint.setText("")
        else:
            self.provider_hint.setText(
                tr(
                    "⚠ No API key set for “{provider}”. "
                    "Fill it in above and click Apply Settings."
                ).format(provider=self.provider_combo.currentText())
            )

    def _provider_key_present(self, api: str) -> bool:
        if api == "openai":
            return True  # local endpoints may not need a key
        env_var = _PROVIDER_KEY_MAPPING.get(api, "")
        edit = self._key_edits.get(env_var)
        return bool(edit and edit.text().strip())

    def _refresh_models(self) -> None:
        if self._model_thread is not None:
            return
        self._apply_config()
        reset_model_caches()
        api = self.provider_combo.currentData()
        self.refresh_models_btn.setEnabled(False)
        self.statusBar().showMessage(tr("Fetching models for {api}…").format(api=api))

        self._model_thread = QThread()
        self._model_worker = ModelFetchWorker(api)
        self._model_worker.moveToThread(self._model_thread)
        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.done.connect(self._on_models_done)
        self._model_worker.failed.connect(self._on_models_failed)
        self._model_worker.done.connect(self._model_thread.quit)
        self._model_worker.failed.connect(self._model_thread.quit)
        self._model_thread.finished.connect(self._cleanup_model_thread)
        self._model_thread.start()

    def _on_models_done(self, models: list) -> None:
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current in models:
            self.model_combo.setCurrentText(current)
        elif models:
            self.model_combo.setCurrentIndex(0)
        self.statusBar().showMessage(
            tr("Loaded {n} models.").format(n=len(models)), 4000
        )

    def _on_models_failed(self, msg: str) -> None:
        QMessageBox.warning(self, tr("Model fetch failed"), msg)
        self.statusBar().showMessage(tr("Model fetch failed."), 4000)

    def _cleanup_model_thread(self) -> None:
        self.refresh_models_btn.setEnabled(True)
        if self._model_thread is not None:
            self._model_thread.wait()
        self._model_thread = None
        self._model_worker = None

    # =============================================================== library
    def _scan_library(self) -> None:
        if self._scan_thread is not None:
            return
        lib = self.calibre_edit.text().strip()
        if not lib or not os.path.isdir(lib):
            QMessageBox.warning(
                self, tr("Invalid library"),
                tr("Set a valid Calibre library path on the Configuration tab."),
            )
            return
        self.scan_btn.setEnabled(False)
        self.statusBar().showMessage(tr("Scanning Calibre library…"))

        self._scan_thread = QThread()
        self._scan_worker = ScanWorker(lib)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.done.connect(self._on_scan_done)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.done.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._cleanup_scan_thread)
        self._scan_thread.start()

    def _on_scan_done(self, books: list) -> None:
        self._books = books
        self._populate_table()
        self.statusBar().showMessage(
            tr("Found {n} books.").format(n=len(books)), 4000
        )

    def _on_scan_failed(self, msg: str) -> None:
        QMessageBox.warning(self, tr("Scan failed"), msg)
        self.statusBar().showMessage(tr("Scan failed."), 4000)

    def _cleanup_scan_thread(self) -> None:
        self.scan_btn.setEnabled(True)
        if self._scan_thread is not None:
            self._scan_thread.wait()
        self._scan_thread = None
        self._scan_worker = None

    def _add_epub(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("Add EPUB files"), _SCRIPT_DIR, "EPUB files (*.epub)"
        )
        for f in files:
            if f not in self._extra_books:
                self._extra_books.append(f)
        if files:
            self._populate_table()

    def _cleanup_ghosts(self) -> None:
        lib = self.calibre_edit.text().strip()
        if not lib or not os.path.isdir(lib):
            QMessageBox.warning(self, tr("Invalid library"), tr("Set a valid library path."))
            return
        confirm = QMessageBox.question(
            self, tr("Cleanup ghost folders"),
            tr(
                "Remove book folders on disk that are not registered in Calibre?\n"
                "This deletes files. Continue?"
            ),
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = calibre_browser.cleanup_ghost_folders(lib)
        QMessageBox.information(
            self, tr("Cleanup done"),
            tr("Removed {n} ghost folder(s).").format(n=removed),
        )

    def _populate_table(self) -> None:
        books = list(getattr(self, "_books", []))
        rows: list[dict] = list(books)
        for path in self._extra_books:
            rows.append(
                {
                    "title": os.path.basename(path),
                    "author": tr("(added file)"),
                    "added_date": "",
                    "epub_path": path,
                    "progress": read_progress(output_json_path(path)),
                }
            )
        self.book_table.setRowCount(len(rows))
        for r, b in enumerate(rows):
            title = QTableWidgetItem(b.get("title", ""))
            title.setData(Qt.ItemDataRole.UserRole, b["epub_path"])
            self.book_table.setItem(r, 0, title)
            self.book_table.setItem(r, 1, QTableWidgetItem(b.get("author", "")))
            self.book_table.setItem(
                r, 2, QTableWidgetItem((b.get("added_date") or "")[:10])
            )
            prog = b.get("progress")
            status = QTableWidgetItem(status_text(prog))
            if prog is not None and prog >= 100:
                status.setForeground(QColor("#2e7d32"))
            elif prog:
                status.setForeground(QColor("#b58900"))
            self.book_table.setItem(r, 3, status)
            init_code = (
                "checking" if self._webdav_config().is_configured()
                else webdav_sync.STATUS_UNCONFIGURED
            )
            self.book_table.setItem(r, 4, webdav_status_item(init_code))
        self.book_count_label.setText(tr("{n} books").format(n=len(rows)))
        self._apply_filter()
        # Fill the WebDAV column asynchronously if a server is configured.
        self._refresh_webdav_status()

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        for r in range(self.book_table.rowCount()):
            title = self.book_table.item(r, 0)
            author = self.book_table.item(r, 1)
            hay = f"{title.text() if title else ''} {author.text() if author else ''}".lower()
            self.book_table.setRowHidden(r, bool(text) and text not in hay)

    def _selected_paths(self) -> list[str]:
        paths: list[str] = []
        for idx in self.book_table.selectionModel().selectedRows():
            item = self.book_table.item(idx.row(), 0)
            if item:
                paths.append(item.data(Qt.ItemDataRole.UserRole))
        return paths

    def _selected_titles(self) -> list[str]:
        titles: list[str] = []
        for idx in self.book_table.selectionModel().selectedRows():
            item = self.book_table.item(idx.row(), 0)
            if item:
                titles.append(item.text())
        return titles

    def _update_push_button_label(self) -> None:
        """Reflect the current book selection in the Push button's label."""
        btn = getattr(self, "push_now_btn", None)
        if btn is None:
            return
        titles = self._selected_titles()
        if not titles:
            btn.setText(tr("Push Selected Book Now"))
        elif len(titles) == 1:
            btn.setText(tr("Push “{title}” Now").format(title=titles[0]))
        else:
            btn.setText(tr("Push {n} Selected Books Now").format(n=len(titles)))

    # ============================================================= analysis
    def _start_analysis(self) -> None:
        if self._proc_thread is not None:
            return
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No books selected"),
                tr("Select one or more books in the table first."),
            )
            return
        self._apply_config()
        # The retry chain is the source of truth; its primary entry drives the
        # client/model handed to the worker (the engine builds the rest lazily).
        chain = self._chain_from_ui()
        if chain.primary is not None:
            api = chain.primary.provider
            model = chain.primary.model
        else:
            api = self.provider_combo.currentData()
            model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.warning(self, tr("No model"), tr("Choose a model on the Configuration tab."))
            return
        if not self._provider_key_present(api):
            QMessageBox.warning(self, tr("Missing API key"), tr("Set the API key for this provider."))
            return

        device = self.device_edit.text().strip()
        auto_push = self.autopush_chk.isChecked()
        webdav_cfg = self._webdav_config()
        webdav_auto = self.webdav_autopush_chk.isChecked()
        self._save_prefs()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.overall_bar.setValue(0)
        self.book_bar.setValue(0)
        ai_client.reset_token_usage()
        self.tabs.setCurrentIndex(2)  # Progress tab
        self._append_log(
            tr("=== Starting batch: {n} book(s) | {api} / {model} ===\n").format(
                n=len(paths), api=api, model=model
            )
        )

        self._proc_thread = QThread()
        self._proc_worker = ProcessWorker(
            paths, api, model, device, auto_push, webdav_cfg, webdav_auto
        )
        self._proc_worker.moveToThread(self._proc_thread)
        self._proc_thread.started.connect(self._proc_worker.run)
        self._proc_worker.progress.connect(self._on_progress)
        self._proc_worker.log.connect(self._append_log)
        self._proc_worker.book_started.connect(self._on_book_started)
        self._proc_worker.book_finished.connect(self._on_book_finished)
        self._proc_worker.pushed.connect(self._on_pushed)
        self._proc_worker.webdav_pushed.connect(self._on_webdav_pushed)
        self._proc_worker.finished.connect(self._proc_thread.quit)
        self._proc_thread.finished.connect(self._on_batch_finished)
        self._proc_thread.start()

    def _stop_analysis(self) -> None:
        if self._proc_worker is not None:
            self._proc_worker.stop()
            self.stop_btn.setEnabled(False)
            self._append_log(tr("Stop requested — stopping as soon as possible…\n"))

    def _on_book_started(self, path: str, index: int, total: int) -> None:
        self._batch_total = total
        self._batch_index = index
        self.current_book_label.setText(os.path.basename(path))
        self.book_bar.setValue(0)
        self.overall_label.setText(tr("Book {index} of {total}").format(index=index, total=total))
        self.overall_bar.setMaximum(total)
        self.overall_bar.setValue(index - 1)

    def _on_book_finished(self, path: str, success: bool, message: str) -> None:
        state = tr("✓ done") if success else tr("✗ failed: {message}").format(message=message)
        self._append_log(f"[{os.path.basename(path)}] {state}\n")
        if hasattr(self, "_batch_index"):
            self.overall_bar.setValue(self._batch_index)
        self._refresh_row_status(path)

    def _on_pushed(self, path: str, success: bool) -> None:
        result = tr("ok") if success else tr("failed")
        self._append_log(
            tr("[{book}] pushed to device: {result}").format(
                book=os.path.basename(path), result=result
            )
            + "\n"
        )

    def _on_progress(self, d: dict) -> None:
        pct = int(d.get("pct") or 0)
        self.book_bar.setValue(max(0, min(100, pct)))
        chunk = d.get("chunk") or 0
        total = d.get("total") or 0
        if total:
            self.chunk_label.setText(f"{chunk} / {total}")  # noqa: i18n
        op = (d.get("op") or "").replace("_", " ")
        if op:
            self.op_label.setText(op)
        stats = d.get("stats") or {}
        if stats:
            self.stat_chars.setText(tr("Characters: {n}").format(n=stats.get('characters', 0)))
            self.stat_locs.setText(tr("Locations: {n}").format(n=stats.get('locations', 0)))
            self.stat_events.setText(tr("Events: {n}").format(n=stats.get('events', 0)))

    def _on_batch_finished(self) -> None:
        if self._proc_thread is not None:
            self._proc_thread.wait()
        self._proc_thread = None
        self._proc_worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.overall_label.setText(tr("Batch complete"))
        self._append_log(tr("=== Batch complete ===\n"))
        self.statusBar().showMessage(tr("Batch complete."), 5000)
        usage = ai_client.get_token_usage()
        if usage:
            dlg = CostSummaryDialog(usage, self)
            dlg.exec()

    def _refresh_row_status(self, path: str) -> None:
        prog = read_progress(output_json_path(path))
        for r in range(self.book_table.rowCount()):
            item = self.book_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == path:
                status = QTableWidgetItem(status_text(prog))
                if prog is not None and prog >= 100:
                    status.setForeground(QColor("#2e7d32"))
                elif prog:
                    status.setForeground(QColor("#b58900"))
                self.book_table.setItem(r, 3, status)
                break

    # ================================================================== log
    def _append_log(self, text: str) -> None:
        self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
        self.log_view.insertPlainText(text)
        if self.autoscroll_chk.isChecked():
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save Log"), os.path.join(_SCRIPT_DIR, "xray_gui.log"),
            tr("Log files (*.log *.txt)"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_view.toPlainText())
            self.statusBar().showMessage(
                tr("Log saved to {path}").format(path=path), 4000
            )
        except OSError as e:
            QMessageBox.warning(self, tr("Save failed"), str(e))

    # ================================================================= sync
    def _test_device(self) -> None:
        device = self.device_edit.text().strip()
        if not device:
            self.device_status.setText(tr("Enter a device IP first."))
            return
        ok, msg = test_device(device)
        self.device_status.setText(("✓ " if ok else "✗ ") + msg)

    def _push_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, tr("No selection"), tr("Select a book in the Books tab."))
            return
        device = self.device_edit.text().strip()
        if not device:
            QMessageBox.warning(self, tr("No device"), tr("Enter a KOReader device IP."))
            return
        for path in paths:
            jp = output_json_path(path)
            if os.path.exists(jp):
                # push_to_koreader prints its result; capture into the log.
                old = sys.stdout
                sys.stdout = _StdoutRedirector(self._append_log)
                try:
                    generator.push_to_koreader(jp, device)
                finally:
                    sys.stdout = old
            else:
                self._append_log(
                    tr("[{book}] no xray_data.json to push.").format(
                        book=os.path.basename(path)
                    )
                    + "\n"
                )
        self.tabs.setCurrentIndex(2)

    # =============================================================== webdav
    def _webdav_config(self) -> webdav_sync.WebDavConfig:
        """Build a WebDavConfig from the current Sync-tab fields."""
        return webdav_sync.WebDavConfig(
            base_url=self.webdav_url_edit.text().strip(),
            username=self.webdav_user_edit.text().strip(),
            password=self.webdav_pass_edit.text(),
        )

    def _on_webdav_credentials_edited(self) -> None:
        """Reset WebDAV login state whenever URL/user/password changes."""
        self._webdav_authenticated = False
        self.webdav_status_label.setText(tr("Not configured."))

    def _browse_webdav_folder(self) -> None:
        """Open the WebDAV folder tree browser and update the URL field."""
        cfg = self._webdav_config()
        if not cfg.is_configured():
            self.webdav_status_label.setText(
                tr("Enter a WebDAV server URL and credentials before browsing.")
            )
            return
        dlg = WebDavFolderDialog(cfg, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.webdav_url_edit.setText(dlg.selected_url())
            self._save_prefs()

    def _test_webdav(self) -> None:
        cfg = self._webdav_config()
        if not cfg.is_configured():
            self.webdav_status_label.setText(tr("Enter a WebDAV server URL first."))
            return
        self.webdav_status_label.setText(tr("Testing…"))
        QApplication.processEvents()
        ok, msg = webdav_sync.test_connection(cfg)
        self._webdav_authenticated = ok
        self.webdav_status_label.setText(("✓ " if ok else "✗ ") + msg)
        self._save_prefs()

    def _all_paths(self) -> list[str]:
        """Every book path currently listed in the Books table."""
        paths: list[str] = []
        for r in range(self.book_table.rowCount()):
            item = self.book_table.item(r, 0)
            if item:
                p = item.data(Qt.ItemDataRole.UserRole)
                if p:
                    paths.append(p)
        return paths

    def _set_webdav_cell(self, path: str, code: str) -> None:
        for r in range(self.book_table.rowCount()):
            item = self.book_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == path:
                self.book_table.setItem(r, 4, webdav_status_item(code))
                break

    def _on_webdav_pushed(self, path: str, ok: bool) -> None:
        self._set_webdav_cell(
            path, webdav_sync.STATUS_SYNCED if ok else webdav_sync.STATUS_ERROR
        )

    def _refresh_webdav_status(self) -> None:
        cfg = self._webdav_config()
        if not cfg.is_configured() or self._webdav_thread is not None:
            return
        paths = self._all_paths()
        if not paths:
            return
        for p in paths:
            self._set_webdav_cell(p, "checking")
        self._webdav_thread = QThread()
        self._webdav_worker = WebDavStatusWorker(cfg, paths)
        self._webdav_worker.moveToThread(self._webdav_thread)
        self._webdav_thread.started.connect(self._webdav_worker.run)
        self._webdav_worker.status.connect(self._set_webdav_cell)
        self._webdav_worker.finished.connect(self._webdav_thread.quit)
        self._webdav_thread.finished.connect(self._cleanup_webdav_thread)
        self._webdav_thread.start()

    def _refresh_selected_webdav_status(self) -> None:
        cfg = self._webdav_config()
        if not cfg.is_configured():
            QMessageBox.warning(
                self, tr("No WebDAV server"),
                tr("Configure a WebDAV server on the Sync tab."),
            )
            return
        if self._webdav_thread is not None:
            self.statusBar().showMessage(tr("WebDAV is busy…"), 3000)
            return
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No selection"), tr("Select a book in the Books tab.")
            )
            return
        for p in paths:
            self._set_webdav_cell(p, "checking")
        self._webdav_thread = QThread()
        self._webdav_worker = WebDavStatusWorker(cfg, paths)
        self._webdav_worker.moveToThread(self._webdav_thread)
        self._webdav_thread.started.connect(self._webdav_worker.run)
        self._webdav_worker.status.connect(self._set_webdav_cell)
        self._webdav_worker.finished.connect(self._webdav_thread.quit)
        self._webdav_thread.finished.connect(self._cleanup_webdav_thread)
        self._webdav_thread.start()

    def _open_local_folder(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No selection"), tr("Select a book in the Books tab.")
            )
            return
        for path in paths:
            sdr = get_sdr_name(path)
            folder = os.path.join(generator.get_xray_base_dir(), sdr)
            os.makedirs(folder, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _delete_local_xray(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No selection"), tr("Select a book in the Books tab.")
            )
            return
        existing = [p for p in paths if os.path.exists(output_json_path(p))]
        if not existing:
            QMessageBox.information(
                self, tr("Delete Local X-Ray"),
                tr("No local X-Ray data found for the selected book(s).")
            )
            return
        reply = QMessageBox.question(
            self, tr("Delete Local X-Ray"),
            tr("Delete local X-Ray data for {n} book(s)?").format(n=len(existing)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for path in existing:
            try:
                os.remove(output_json_path(path))
            except OSError:
                pass
            self._refresh_row_status(path)
        self.statusBar().showMessage(
            tr("Deleted X-Ray data for {n} book(s).").format(n=len(existing)), 4000
        )

    def _webdav_upload_selected(self) -> None:
        self._start_webdav_op("upload")

    def _webdav_download_selected(self) -> None:
        self._start_webdav_op("download")

    def _webdav_delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No selection"), tr("Select a book in the Books tab.")
            )
            return
        titles = self._selected_titles()
        preview = ", ".join(f'"{t}"' for t in titles[:3])
        if len(titles) > 3:
            preview += tr(" and {n} more").format(n=len(titles) - 3)
        confirm = QMessageBox.question(
            self,
            tr("Delete Selected from WebDAV"),
            tr("Delete the remote X-Ray folder for {preview} from WebDAV?\nThis cannot be undone.").format(
                preview=preview
            ),
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._start_webdav_op("delete")

    def _start_webdav_op(self, op: str) -> None:
        cfg = self._webdav_config()
        if not cfg.is_configured():
            QMessageBox.warning(
                self, tr("No WebDAV server"),
                tr("Configure a WebDAV server on the Sync tab."),
            )
            return
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No selection"), tr("Select a book in the Books tab.")
            )
            return
        if self._webdav_thread is not None:
            self.statusBar().showMessage(tr("WebDAV is busy…"), 3000)
            return
        self._save_prefs()
        self.tabs.setCurrentIndex(2)  # show the log
        n = len(paths)
        msg = (
            tr("Uploading {n} book(s) to WebDAV…") if op == "upload"
            else tr("Downloading {n} book(s) from WebDAV…") if op == "download"
            else tr("Deleting {n} book(s) from WebDAV…")
        )
        self._append_log(msg.format(n=n) + "\n")
        for p in paths:
            self._set_webdav_cell(p, "checking")

        self._webdav_thread = QThread()
        self._webdav_worker = WebDavOpWorker(cfg, paths, op)
        self._webdav_worker.moveToThread(self._webdav_thread)
        self._webdav_thread.started.connect(self._webdav_worker.run)
        self._webdav_worker.status.connect(self._set_webdav_cell)
        self._webdav_worker.log.connect(self._append_log)
        self._webdav_worker.finished.connect(self._webdav_thread.quit)
        self._webdav_thread.finished.connect(self._cleanup_webdav_thread)
        self._webdav_thread.start()

    def _cleanup_webdav_thread(self) -> None:
        if self._webdav_thread is not None:
            self._webdav_thread.wait()
        self._webdav_thread = None
        self._webdav_worker = None

    # ============================================================== results
    def _open_result_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Open xray_data.json"),
            generator.get_xray_base_dir(), tr("JSON files (*.json)"),
        )
        if path:
            self._load_result(path)

    def _load_selected_result(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, tr("No selection"), tr("Select a book in the Books tab."))
            return
        jp = output_json_path(paths[0])
        if not os.path.exists(jp):
            QMessageBox.information(self, tr("No result"), tr("No xray_data.json for that book yet."))
            return
        self._load_result(jp)
        self.tabs.setCurrentIndex(4)

    def _load_result(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.warning(self, tr("Load failed"), str(e))
            return
        self._result_data = data
        title = data.get("book_title", tr("Unknown"))
        author = data.get("author", "")
        prog = data.get("analysis_progress", 0)
        self.result_title.setText(
            tr("{title} — {author}  ({prog}%)").format(title=title, author=author, prog=prog)
        )

        self.result_tree.clear()

        chars = QTreeWidgetItem([tr("Characters"), str(len(data.get("characters", [])))])
        for c in data.get("characters", []):
            node = QTreeWidgetItem([c.get("name", ""), ""])
            node.setData(0, Qt.ItemDataRole.UserRole, ("character", c))
            chars.addChild(node)
        self.result_tree.addTopLevelItem(chars)

        locs = QTreeWidgetItem([tr("Locations"), str(len(data.get("locations", [])))])
        for loc in data.get("locations", []):
            node = QTreeWidgetItem([loc.get("name", ""), ""])
            node.setData(0, Qt.ItemDataRole.UserRole, ("location", loc))
            locs.addChild(node)
        self.result_tree.addTopLevelItem(locs)

        timeline = QTreeWidgetItem([tr("Timeline"), str(len(data.get("timeline", [])))])
        for ev in data.get("timeline", []):
            label = ev.get("event") or ev.get("description") or str(ev)
            node = QTreeWidgetItem([str(label)[:60], ""])
            node.setData(0, Qt.ItemDataRole.UserRole, ("event", ev))
            timeline.addChild(node)
        self.result_tree.addTopLevelItem(timeline)

        themes = QTreeWidgetItem([tr("Themes"), str(len(data.get("themes", [])))])
        for t in data.get("themes", []):
            themes.addChild(QTreeWidgetItem([str(t), ""]))
        self.result_tree.addTopLevelItem(themes)

        meta = QTreeWidgetItem([tr("Summary / Author"), ""])
        meta.setData(
            0, Qt.ItemDataRole.UserRole,
            ("summary", {
                "summary": data.get("summary", ""),
                "author_bio": data.get("author_bio", ""),
            }),
        )
        self.result_tree.addTopLevelItem(meta)
        self.result_tree.expandToDepth(0)

    def _on_result_item(self, item: QTreeWidgetItem, _column: int) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            self.result_detail.clear()
            return
        kind, obj = payload
        if kind == "character":
            lines = [f"# {obj.get('name', '')}", ""]
            descs = obj.get("descriptions", [])
            if descs:
                lines.append(descs[-1].get("text", "") if isinstance(descs[-1], dict) else str(descs[-1]))
            for ev in obj.get("events", []):
                pct = ev.get("absolute_percent", "?")
                lines.append(f"  • [{pct}%] {ev.get('event') or ev.get('description') or ''}")
            self.result_detail.setPlainText("\n".join(lines))
        elif kind == "location":
            descs = obj.get("descriptions", [])
            text = descs[-1].get("text", "") if descs and isinstance(descs[-1], dict) else ""
            self.result_detail.setPlainText(f"# {obj.get('name', '')}\n\n{text}")
        elif kind == "event":
            self.result_detail.setPlainText(json.dumps(obj, ensure_ascii=False, indent=2))
        elif kind == "summary":
            self.result_detail.setPlainText(
                f"# Summary\n\n{obj.get('summary', '')}\n\n"
                f"# Author\n\n{obj.get('author_bio', '')}"
            )

    # =============================================================== close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._proc_thread is not None:
            reply = QMessageBox.question(
                self, tr("Analysis running"),
                tr("Analysis is still running. Stop and quit?"),
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._proc_worker is not None:
                self._proc_worker.stop()
            self._proc_thread.quit()
            self._proc_thread.wait(3000)
        self._save_prefs()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("X-Ray Generator")
    # Block mouse-wheel scrolling from editing spin box / combo box values.
    wheel_guard = _WheelGuard(app)
    app.installEventFilter(wheel_guard)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
