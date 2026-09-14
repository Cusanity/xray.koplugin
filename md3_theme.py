#!/usr/bin/env python3
"""
Material Design 3 (MD3 / Material You) Theme Engine for PyQt6.

Provides Google Material 3 tokens, dynamic light and dark color schemes,
elevation surfaces, concentric radii, and a comprehensive Qt Style Sheet (QSS)
for desktop and compact viewports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PyQt6.QtGui import QColor, QPalette

ThemeMode = Literal["light", "dark", "system"]

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _ensure_assets() -> dict[str, str]:
    """Ensure vector SVG icons exist on disk and return POSIX paths for QSS."""
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    check_svg = _ASSETS_DIR / "md3_check.svg"
    if not check_svg.exists():
        check_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24">'
            '<path fill="#ffffff" d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
            encoding="utf-8",
        )
    arrow_light = _ASSETS_DIR / "md3_arrow_down_light.svg"
    if not arrow_light.exists():
        arrow_light.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24">'
            '<path fill="#444746" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>',
            encoding="utf-8",
        )
    arrow_dark = _ASSETS_DIR / "md3_arrow_down_dark.svg"
    if not arrow_dark.exists():
        arrow_dark.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24">'
            '<path fill="#C4C7C5" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>',
            encoding="utf-8",
        )
    arrow_up_light = _ASSETS_DIR / "md3_arrow_up_light.svg"
    if not arrow_up_light.exists():
        arrow_up_light.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24">'
            '<path fill="#444746" d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6 1.41 1.41z"/></svg>',
            encoding="utf-8",
        )
    arrow_up_dark = _ASSETS_DIR / "md3_arrow_up_dark.svg"
    if not arrow_up_dark.exists():
        arrow_up_dark.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24">'
            '<path fill="#C4C7C5" d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6 1.41 1.41z"/></svg>',
            encoding="utf-8",
        )
    return {
        "check": check_svg.as_posix(),
        "arrow_light": arrow_light.as_posix(),
        "arrow_dark": arrow_dark.as_posix(),
        "arrow_up_light": arrow_up_light.as_posix(),
        "arrow_up_dark": arrow_up_dark.as_posix(),
    }


@dataclass(frozen=True)
class MD3Palette:
    """Google Material Design 3 color tokens."""

    # Primary brand roles
    primary: str
    on_primary: str
    primary_container: str
    on_primary_container: str

    # Secondary roles
    secondary: str
    on_secondary: str
    secondary_container: str
    on_secondary_container: str

    # Tertiary roles
    tertiary: str
    on_tertiary: str
    tertiary_container: str
    on_tertiary_container: str

    # Surface & Elevation hierarchy (Luminance stack)
    surface: str
    surface_dim: str
    surface_bright: str
    surface_container_lowest: str
    surface_container_low: str
    surface_container: str
    surface_container_high: str
    surface_container_highest: str

    # Content & Outlines
    on_surface: str
    on_surface_variant: str
    outline: str
    outline_variant: str

    # Status & Feedback
    error: str
    on_error: str
    error_container: str
    on_error_container: str

    success: str
    on_success: str
    success_container: str
    on_success_container: str

    warning: str
    on_warning: str
    warning_container: str
    on_warning_container: str

    info: str
    on_info: str
    info_container: str
    on_info_container: str

    # Shadows & Scrim
    shadow: str
    scrim: str


MD3_LIGHT_PALETTE = MD3Palette(
    primary="#0B57D0",
    on_primary="#FFFFFF",
    primary_container="#D3E3FD",
    on_primary_container="#041E49",
    secondary="#00639B",
    on_secondary="#FFFFFF",
    secondary_container="#C2E7FF",
    on_secondary_container="#001D35",
    tertiary="#00677D",
    on_tertiary="#FFFFFF",
    tertiary_container="#B9ECFF",
    on_tertiary_container="#001F27",
    surface="#F8F9FA",
    surface_dim="#D8DADF",
    surface_bright="#F8F9FA",
    surface_container_lowest="#FFFFFF",
    surface_container_low="#F0F4F9",
    surface_container="#EDF2F7",
    surface_container_high="#E7ECF3",
    surface_container_highest="#E0E6EE",
    on_surface="#1F1F1F",
    on_surface_variant="#444746",
    outline="#747775",
    outline_variant="#C4C7C5",
    error="#BA1A1A",
    on_error="#FFFFFF",
    error_container="#FFDAD6",
    on_error_container="#410002",
    success="#137333",
    on_success="#FFFFFF",
    success_container="#C4EED0",
    on_success_container="#072711",
    warning="#B06000",
    on_warning="#FFFFFF",
    warning_container="#FFE0B2",
    on_warning_container="#2E1500",
    info="#00639B",
    on_info="#FFFFFF",
    info_container="#C2E7FF",
    on_info_container="#001D35",
    shadow="#000000",
    scrim="#000000",
)

MD3_DARK_PALETTE = MD3Palette(
    primary="#A8C7FA",
    on_primary="#042F73",
    primary_container="#0842A0",
    on_primary_container="#D3E3FD",
    secondary="#7FCFFF",
    on_secondary="#003355",
    secondary_container="#004A77",
    on_secondary_container="#C2E7FF",
    tertiary="#61D6F6",
    on_tertiary="#003542",
    tertiary_container="#004E60",
    on_tertiary_container="#B9ECFF",
    surface="#111318",
    surface_dim="#111318",
    surface_bright="#37393E",
    surface_container_lowest="#0C0E13",
    surface_container_low="#191C20",
    surface_container="#1D2024",
    surface_container_high="#282A2F",
    surface_container_highest="#33353A",
    on_surface="#E2E2E6",
    on_surface_variant="#C4C7C5",
    outline="#8E918F",
    outline_variant="#444746",
    error="#FFB4AB",
    on_error="#690005",
    error_container="#93000A",
    on_error_container="#FFDAD6",
    success="#8AE39C",
    on_success="#003915",
    success_container="#005322",
    on_success_container="#C4EED0",
    warning="#FFB77C",
    on_warning="#4D2700",
    warning_container="#6E3900",
    on_warning_container="#FFE0B2",
    info="#7FCFFF",
    on_info="#003355",
    info_container="#004A77",
    on_info_container="#C2E7FF",
    shadow="#000000",
    scrim="#000000",
)


def get_palette(is_dark: bool = False) -> MD3Palette:
    """Return the active MD3 palette for light or dark mode."""
    return MD3_DARK_PALETTE if is_dark else MD3_LIGHT_PALETTE


def generate_stylesheet(is_dark: bool = False) -> str:
    """
    Generate a full Google Material Design 3 Qt StyleSheet.

    Adheres to:
    1. Tonal Surface Stack (no 1px high-contrast divider traps)
    2. Concentric nested radii math: R_inner = max(0, R_outer - padding)
    3. Proper typography scale with tabular numbers on metrics & tables
    4. MD3 button states: filled, tonal, outlined, text
    5. MD3 linear progress indicators
    6. MD3 primary tabs with full pill indicators
    7. High accessibility contrast ratios (> 7:1)
    """
    p = get_palette(is_dark)
    assets = _ensure_assets()
    arrow_path = assets["arrow_dark"] if is_dark else assets["arrow_light"]
    arrow_up_path = assets["arrow_up_dark"] if is_dark else assets["arrow_up_light"]
    check_path = assets["check"]

    return f"""
/* =========================================================================
   GOOGLE MATERIAL DESIGN 3 - GLOBAL APPLICATION STYLESHEET
   ========================================================================= */

* {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: {p.on_surface};
    selection-background-color: {p.primary_container};
    selection-color: {p.on_primary_container};
    outline: none;
}}

/* Window & Base Surfaces */
QMainWindow {{
    background-color: {p.surface};
    color: {p.on_surface};
}}

QDialog, QWizard {{
    background-color: {p.surface};
    color: {p.on_surface};
    border-radius: 28px;
}}

QTabWidget {{
    background-color: {p.surface};
}}

/* Scroll Areas & Viewports (Zero unwanted white/black blocks) */
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget,
QStackedWidget {{
    background-color: transparent;
    border: none;
}}


/* =========================================================================
   MD3 PRIMARY TABS (Navigation & Destinations)
   ========================================================================= */

QTabWidget::pane {{
    border: 1px solid {p.surface_container_high};
    background-color: {p.surface_container_lowest};
    border-radius: 16px;
    margin-top: 6px;
    padding: 12px;
}}

QTabBar {{
    background-color: {p.surface_container_low};
    border-radius: 20px;
    padding: 4px;
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {p.on_surface_variant};
    font-weight: 600;
    font-size: 13px;
    padding: 8px 20px;
    margin: 2px 4px;
    border-radius: 16px;
    min-height: 24px;
    border: none;
}}

QTabBar::tab:hover {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
}}

QTabBar::tab:selected {{
    background-color: {p.primary_container};
    color: {p.on_primary_container};
    font-weight: 700;
}}

/* =========================================================================
   MD3 CARDS & CONTAINERS (Bento Card Elevation)
   ========================================================================= */

QGroupBox {{
    background-color: {p.surface_container_low};
    border: 1px solid {p.surface_container_high};
    border-radius: 16px;
    margin-top: 16px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
    font-size: 13px;
    color: {p.primary};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    top: 2px;
    padding: 2px 8px;
    background-color: {p.surface_container_low};
    border-radius: 8px;
    color: {p.primary};
    font-weight: 700;
}}

/* =========================================================================
   MD3 BUTTONS (Filled, Tonal, Outlined, Text)
   ========================================================================= */

QPushButton {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
    border: 1px solid {p.outline_variant};
    border-radius: 18px;
    padding: 0px 16px;
    font-weight: 600;
    font-size: 13px;
    min-height: 36px;
}}

QPushButton:hover {{
    background-color: {p.surface_container_highest};
    border-color: {p.outline};
}}

QPushButton:pressed {{
    background-color: {p.surface_container};
}}

QPushButton:disabled {{
    background-color: {p.surface_container_low};
    color: {p.outline};
    border-color: {p.surface_container_high};
}}

/* MD3 Filled Button (Primary Call-to-Action) */
QPushButton[role="filled"],
QPushButton#start_btn,
QPushButton#scan_btn,
QPushButton#apply_btn,
QDialogButtonBox QPushButton:default,
QDialogButtonBox QPushButton[role="filled"],
QWizard QPushButton[role="filled"] {{
    background-color: {p.primary};
    color: {p.on_primary};
    border: 1px solid {p.primary};
    border-radius: 18px;
    min-height: 36px;
    font-weight: 700;
    padding: 0px 22px;
}}

QPushButton[role="filled"]:hover,
QPushButton#start_btn:hover,
QPushButton#scan_btn:hover,
QPushButton#apply_btn:hover,
QDialogButtonBox QPushButton:default:hover,
QWizard QPushButton[role="filled"]:hover {{
    background-color: {p.secondary};
    border-color: {p.secondary};
}}

QPushButton[role="filled"]:pressed,
QPushButton#start_btn:pressed,
QPushButton#scan_btn:pressed,
QPushButton#apply_btn:pressed {{
    background-color: {p.primary_container};
    color: {p.on_primary_container};
    border-color: {p.primary_container};
}}

/* MD3 Tonal Button (Secondary Action) */
QPushButton[role="tonal"],
QPushButton#refresh_models_btn,
QPushButton#webdav_refresh_btn,
QPushButton#webdav_upload_btn,
QPushButton#webdav_download_btn {{
    background-color: {p.secondary_container};
    color: {p.on_secondary_container};
    border: 1px solid {p.secondary_container};
    border-radius: 18px;
    min-height: 36px;
    font-weight: 600;
    padding: 0px 18px;
}}

QPushButton[role="tonal"]:hover,
QPushButton#refresh_models_btn:hover,
QPushButton#webdav_refresh_btn:hover,
QPushButton#webdav_upload_btn:hover,
QPushButton#webdav_download_btn:hover {{
    background-color: {p.primary_container};
    color: {p.on_primary_container};
    border-color: {p.primary_container};
}}

/* MD3 Outlined Button */
QPushButton[role="outlined"],
QWizard QPushButton[role="outlined"] {{
    background-color: transparent;
    color: {p.primary};
    border: 1px solid {p.outline};
    border-radius: 18px;
    min-height: 36px;
    font-weight: 600;
    padding: 0px 16px;
}}

QPushButton[role="outlined"]:hover,
QWizard QPushButton[role="outlined"]:hover {{
    background-color: {p.surface_container_high};
    border-color: {p.primary};
}}

/* MD3 Danger / Destructive Button */
QPushButton[role="danger"],
QPushButton#delete_xray_btn,
QPushButton#webdav_delete_btn,
QPushButton#stop_btn {{
    background-color: {p.error_container};
    color: {p.on_error_container};
    border: 1px solid {p.error_container};
    border-radius: 18px;
    min-height: 36px;
    font-weight: 600;
    padding: 0px 18px;
}}

QPushButton[role="danger"]:hover,
QPushButton#delete_xray_btn:hover,
QPushButton#webdav_delete_btn:hover,
QPushButton#stop_btn:hover {{
    background-color: {p.error};
    color: {p.on_error};
    border-color: {p.error};
}}

/* MD3 Text Button */
QPushButton[role="text"],
QWizard QPushButton[role="text"] {{
    background-color: transparent;
    color: {p.primary};
    border: 1px solid transparent;
    border-radius: 18px;
    min-height: 36px;
    font-weight: 600;
    padding: 0px 12px;
}}

QPushButton[role="text"]:hover,
QWizard QPushButton[role="text"]:hover {{
    background-color: {p.surface_container_high};
}}

/* Dialog & Wizard Buttons */
QDialogButtonBox QPushButton {{
    border-radius: 18px;
    min-height: 36px;
    padding: 0px 20px;
    font-weight: 600;
}}

QWizard QPushButton {{
    border-radius: 18px;
    min-height: 36px;
    padding: 0px 18px;
    font-weight: 600;
}}

/* MD3 Category Navigation Bar & Segmented Filter Pills */
#configCategoryBar {{
    background-color: {p.surface_container_lowest};
    border-bottom: 1px solid {p.outline_variant};
    padding: 6px 12px;
}}

QPushButton[role="category-pill"] {{
    background-color: {p.surface_container};
    color: {p.on_surface_variant};
    border: 1px solid {p.outline_variant};
    border-radius: 18px;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 600;
    min-height: 24px;
}}

QPushButton[role="category-pill"]:hover {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
    border-color: {p.outline};
}}

QPushButton[role="category-pill"]:checked,
QPushButton[role="category-pill"][checked="true"] {{
    background-color: {p.secondary_container};
    color: {p.on_secondary_container};
    border: 1.5px solid {p.primary};
    font-weight: 700;
}}

/* Sticky Action Bar (Edge-to-edge docked bottom bar) */
#configActionBar {{
    background-color: {p.surface_container_low};
    border-top: 1px solid {p.outline_variant};
    border-left: none;
    border-right: none;
    border-bottom: none;
    border-radius: 0px;
    padding: 0px;
}}

/* =========================================================================
   MD3 TEXT FIELDS & INPUTS (Rounded Pill & Outlined)
   ========================================================================= */

QLineEdit, QComboBox {{
    background-color: {p.surface_container_lowest};
    color: {p.on_surface};
    border: 1px solid {p.outline};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    min-height: 20px;
}}

QLineEdit:focus, QComboBox:focus {{
    border: 2px solid {p.primary};
    padding: 6px 11px;
}}

QLineEdit:disabled, QComboBox:disabled {{
    background-color: {p.surface_container_low};
    color: {p.outline};
    border-color: {p.surface_container_high};
}}

/* QSpinBox & QDoubleSpinBox with dedicated MD3 vector subcontrols (fixes DEF-01) */
QSpinBox, QDoubleSpinBox {{
    background-color: {p.surface_container_lowest};
    color: {p.on_surface};
    border: 1px solid {p.outline};
    border-radius: 8px;
    padding: 6px 26px 6px 10px;
    font-size: 13px;
    min-height: 20px;
}}

QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid {p.primary};
    padding: 5px 25px 5px 9px;
}}

QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {p.surface_container_low};
    color: {p.outline};
    border-color: {p.surface_container_high};
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    height: 15px;
    border: none;
    border-top-right-radius: 7px;
    background-color: transparent;
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
    background-color: {p.surface_container_high};
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    height: 15px;
    border: none;
    border-bottom-right-radius: 7px;
    background-color: transparent;
}}

QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {p.surface_container_high};
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{arrow_up_path}");
    width: 10px;
    height: 10px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{arrow_path}");
    width: 10px;
    height: 10px;
}}

QPlainTextEdit, QTextEdit {{
    background-color: {p.surface_container_lowest};
    color: {p.on_surface};
    border: 1px solid {p.outline};
    border-radius: 12px;
    padding: 10px;
}}

QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {p.primary};
    padding: 9px;
}}

/* QComboBox Styling */
QComboBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0px 4px;
    margin: 0px;
    color: {p.on_surface};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: none;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}

QComboBox::down-arrow {{
    image: url("{arrow_path}");
    width: 14px;
    height: 14px;
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
    border: 1px solid {p.outline_variant};
    border-radius: 12px;
    padding: 6px;
    selection-background-color: {p.primary_container};
    selection-color: {p.on_primary_container};
}}

/* =========================================================================
   MD3 DATA TABLES & TREE VIEWS (Concentric Radii & Tabular Figures)
   ========================================================================= */

QTableWidget, QTreeWidget {{
    background-color: {p.surface_container_lowest};
    alternate-background-color: {p.surface_container_low};
    border: 1px solid {p.surface_container_high};
    border-radius: 8px;
    gridline-color: {p.surface_container_high};
    color: {p.on_surface};
}}

QTableWidget::item, QTreeWidget::item {{
    padding: 2px 8px;
    border-bottom: 1px solid {p.surface_container_low};
    min-height: 28px;
}}

QTableWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {p.surface_container_low};
}}

QTableWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {p.secondary_container};
    color: {p.on_secondary_container};
    font-weight: 600;
}}

/* Embedded in-table inputs & buttons */
QTableWidget QComboBox {{
    background-color: {p.surface_container_lowest};
    color: {p.on_surface};
    border: 1px solid {p.outline_variant};
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 12px;
    min-height: 24px;
    max-height: 28px;
}}

QTableWidget QComboBox:focus {{
    border: 1.5px solid {p.primary};
}}

QTableWidget QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: none;
}}

QTableWidget QSpinBox, QTableWidget QDoubleSpinBox {{
    background-color: transparent;
    color: {p.on_surface};
    border: 1px solid {p.outline_variant};
    border-radius: 6px;
    padding: 2px 4px;
    font-size: 12px;
    min-height: 24px;
    max-height: 28px;
}}

QTableWidget QSpinBox:focus, QTableWidget QDoubleSpinBox:focus {{
    border: 1.5px solid {p.primary};
}}

QTableWidget QSpinBox QLineEdit, QTableWidget QDoubleSpinBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
    color: {p.on_surface};
}}

QPushButton#chain_balance_btn {{
    background-color: {p.surface_container_high};
    color: {p.primary};
    border: 1px solid {p.outline_variant};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
    min-height: 24px;
    max-height: 24px;
}}

QPushButton#chain_balance_btn:hover {{
    background-color: {p.surface_container_highest};
    border-color: {p.primary};
}}

QHeaderView {{
    background-color: transparent;
    border: none;
}}

QHeaderView::section {{
    background-color: {p.surface_container};
    color: {p.on_surface_variant};
    font-weight: 700;
    font-size: 12px;
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid {p.outline_variant};
}}

QHeaderView::section:first {{
    border-top-left-radius: 7px;
}}

QHeaderView::section:last {{
    border-top-right-radius: 7px;
}}

/* =========================================================================
   MD3 LINEAR PROGRESS INDICATOR
   ========================================================================= */

QProgressBar {{
    background-color: {p.surface_container_highest};
    border: none;
    border-radius: 6px;
    height: 10px;
    text-align: center;
    font-weight: 600;
    font-size: 10px;
    color: {p.on_surface_variant};
}}

QProgressBar::chunk {{
    background-color: {p.primary};
    border-radius: 6px;
}}

/* =========================================================================
   MD3 CHECKBOXES & RADIO BUTTONS
   ========================================================================= */

QCheckBox {{
    spacing: 10px;
    font-weight: 500;
    color: {p.on_surface};
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {p.outline};
    border-radius: 4px;
    background-color: {p.surface_container_lowest};
}}

QCheckBox::indicator:hover {{
    border-color: {p.primary};
    background-color: {p.surface_container_high};
}}

QCheckBox::indicator:checked {{
    border-color: {p.primary};
    background-color: {p.primary};
    image: url("{check_path}");
}}

/* =========================================================================
   MD3 SCROLLBARS (Minimal Rounded Aesthetics)
   ========================================================================= */

QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 4px 0 4px 0;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background-color: {p.outline_variant};
    min-height: 32px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {p.outline};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    border: none;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    margin: 0 4px 0 4px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal {{
    background-color: {p.outline_variant};
    min-width: 32px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {p.outline};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
    border: none;
}}

/* =========================================================================
   MENUBAR, MENUS, TOOLTIPS & STATUSBAR
   ========================================================================= */

QMenuBar {{
    background-color: {p.surface_container_low};
    color: {p.on_surface};
    border-bottom: 1px solid {p.surface_container_high};
    padding: 4px 6px;
    font-weight: 500;
}}

QMenuBar::item {{
    background-color: transparent;
    padding: 6px 12px;
    border-radius: 12px;
}}

QMenuBar::item:selected {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
}}

QMenu {{
    background-color: {p.surface_container_high};
    color: {p.on_surface};
    border: 1px solid {p.surface_container_highest};
    border-radius: 14px;
    padding: 8px 4px;
}}

QMenu::item {{
    padding: 8px 24px;
    border-radius: 8px;
    margin: 2px 4px;
}}

QMenu::item:selected {{
    background-color: {p.primary_container};
    color: {p.on_primary_container};
}}

QToolTip {{
    background-color: {p.surface_container_highest};
    color: {p.on_surface};
    border: 1px solid {p.outline_variant};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}

QStatusBar {{
    background-color: {p.surface_container_low};
    color: {p.on_surface_variant};
    border-top: 1px solid {p.surface_container_high};
    font-size: 12px;
    padding: 4px 8px;
}}

/* =========================================================================
   SPLITTER & DIALOGS
   ========================================================================= */

QSplitter::handle {{
    background-color: {p.surface_container_high};
    width: 3px;
    height: 3px;
}}

/* Custom MD3 Badge & Chip Classes */
.md3-chip {{
    background-color: {p.surface_container_high};
    color: {p.on_surface_variant};
    border: 1px solid {p.outline_variant};
    border-radius: 14px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}}

.md3-badge-success {{
    background-color: {p.success_container};
    color: {p.on_success_container};
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 600;
}}

.md3-badge-warning {{
    background-color: {p.warning_container};
    color: {p.on_warning_container};
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 600;
}}

.md3-badge-error {{
    background-color: {p.error_container};
    color: {p.on_error_container};
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 600;
}}

.md3-badge-info {{
    background-color: {p.info_container};
    color: {p.on_info_container};
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 600;
}}

.md3-tabular {{
    font-family: "Cascadia Code", Consolas, "Segoe UI", monospace;
}}
"""


def apply_theme(app_or_widget, is_dark: bool = False) -> None:
    """Apply the MD3 stylesheet and palette to the given QApplication or QWidget."""
    if hasattr(app_or_widget, "setStyle"):
        app_or_widget.setStyle("Fusion")

    p = get_palette(is_dark)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(p.surface))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p.on_surface))
    palette.setColor(QPalette.ColorRole.Base, QColor(p.surface_container_lowest))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface_container_low))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface_container_highest))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p.on_surface))
    palette.setColor(QPalette.ColorRole.Text, QColor(p.on_surface))
    palette.setColor(QPalette.ColorRole.Button, QColor(p.surface_container))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(p.on_surface))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(p.primary))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(p.on_primary))
    if hasattr(app_or_widget, "setPalette"):
        app_or_widget.setPalette(palette)

    app_or_widget.setStyleSheet(generate_stylesheet(is_dark))
