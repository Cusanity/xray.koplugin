#!/usr/bin/env python3
"""
Automated Multi-Viewport Screenshot Capture Suite for X-Ray Generator GUI.

Captures pixel-perfect screenshots of all tabs, dialogs, and themes across:
1. Desktop Viewport (1440 x 900)
2. Compact / Mobile Viewport (760 x 600 / 412 x 915)
3. Light and Dark MD3 Themes
4. Dialogs (Setup Wizard, Cost Summary, WebDAV Folder Browser)

Stores outputs directly into the UI/UX review directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

# Ensure local imports work
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import generator_gui
import md3_theme
from webdav_sync import WebDavConfig

REVIEW_DIR = Path(r"C:\Users\-GALACTUS-\Desktop\XRayGenerator_UI_UX_Review_Signoff")
REVIEW_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_BOOKS = [
    {
        "title": "The Three-Body Problem",
        "author": "Liu Cixin",
        "added_date": "2026-08-15",
        "epub_path": "sample/three_body.epub",
        "progress": 100,
    },
    {
        "title": "The Dark Forest",
        "author": "Liu Cixin",
        "added_date": "2026-08-18",
        "epub_path": "sample/dark_forest.epub",
        "progress": 45,
    },
    {
        "title": "Death's End",
        "author": "Liu Cixin",
        "added_date": "2026-08-20",
        "epub_path": "sample/deaths_end.epub",
        "progress": 0,
    },
    {
        "title": "Dune",
        "author": "Frank Herbert",
        "added_date": "2026-08-22",
        "epub_path": "sample/dune.epub",
        "progress": 82,
    },
    {
        "title": "Foundation and Empire",
        "author": "Isaac Asimov",
        "added_date": "2026-08-25",
        "epub_path": "sample/foundation.epub",
        "progress": 100,
    },
]

SAMPLE_RESULTS = {
    "book": "The Three-Body Problem",
    "author": "Liu Cixin",
    "analysis_progress": 100,
    "characters": [
        {
            "name": "Ye Wenjie",
            "descriptions": [
                {
                    "percent": 10,
                    "text": "Astrophysicist who first contacts the Trisolarans from Red Coast Base.",
                }
            ],
            "events": [
                {"absolute_percent": 12, "event": "Witnesses her father's persecution at Tsinghua."},
                {"absolute_percent": 35, "event": "Transmits message to Trisolaris via solar amplification."},
            ],
        },
        {
            "name": "Wang Miao",
            "descriptions": [
                {
                    "percent": 25,
                    "text": "Nanomaterials researcher who sees the countdown in his vision and plays Three-Body VR.",
                }
            ],
            "events": [
                {"absolute_percent": 20, "event": "First experiences the visual countdown."},
                {"absolute_percent": 68, "event": "Assists Operation Guzheng using nanofilament wire."},
            ],
        },
        {
            "name": "Shi Qiang (Da Shi)",
            "descriptions": [
                {
                    "percent": 30,
                    "text": "Pragmatic, cigarette-smoking detective and battle commander.",
                }
            ],
            "events": [
                {"absolute_percent": 28, "event": "Convinces Wang Miao to continue investigating Frontiers of Science."},
                {"absolute_percent": 95, "event": "Brings Wang and Ding Yi to look at locusts in the field."},
            ],
        },
    ],
    "locations": [
        {
            "name": "Red Coast Base",
            "descriptions": [{"percent": 15, "text": "Top-secret military radar station on Radar Peak."}],
        },
        {
            "name": "Panama Canal",
            "descriptions": [{"percent": 75, "text": "Site of Operation Guzheng targeting Judgment Day."}],
        },
    ],
    "timeline": [
        {"sequence": 1, "character": "Ye Wenjie", "event": "Red Coast transmission initiated."},
        {"sequence": 2, "character": "Wang Miao", "event": "Three-Body VR game level completed."},
        {"sequence": 3, "character": "Shi Qiang", "event": "Operation Guzheng planned."},
    ],
    "themes": [
        {"name": "Cosmic Sociology", "description": "The universe as a dark forest with scarce resources."},
        {"name": "Scientific Hubris", "description": "Limits of human perception against sophon blockades."},
    ],
    "summary": "First contact with an alien civilization leads humanity into existential crisis.",
    "author_bio": "Liu Cixin is China's premier science-fiction author and Hugo Award winner.",
}


def capture_all() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("X-Ray Generator")
    app.setStyle("Fusion")

    # Apply MD3 Light Theme first
    md3_theme.apply_theme(app, False)

    win = generator_gui.MainWindow()
    win.resize(1440, 900)
    win.show()
    app.processEvents()

    # Populate sample data
    win._books = list(SAMPLE_BOOKS)
    win._populate_table()

    # Populate Progress tab
    win.overall_bar.setValue(68)
    win.overall_label.setText("Batch processing: 3 of 5 books completed")
    win.current_book_label.setText("The Dark Forest (Liu Cixin)")
    win.book_bar.setValue(45)
    win.chunk_label.setText("Chunk 4 of 9")
    win.op_label.setText("Extracting characters & timeline events...")
    win.stat_chars.setText("Characters: 42")
    win.stat_locs.setText("Locations: 18")
    win.stat_events.setText("Events: 104")
    win.log_view.setPlainText(
        "[15:20:01] Processing 'The Three-Body Problem' -> Complete (100%)\n"
        "[15:21:40] Processing 'The Dark Forest' -> Chunk 1/9 [OK]\n"
        "[15:22:15] Processing 'The Dark Forest' -> Chunk 2/9 [OK]\n"
        "[15:22:50] Processing 'The Dark Forest' -> Chunk 3/9 [OK]\n"
        "[15:23:25] Processing 'The Dark Forest' -> Chunk 4/9 in flight via Claude 3.5 Sonnet...\n"
    )

    # Populate Results tab
    temp_json = REVIEW_DIR / "sample_xray_data.json"
    with open(temp_json, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_RESULTS, f, ensure_ascii=False, indent=2)
    win._load_result(str(temp_json))
    if win.result_tree.topLevelItemCount() > 0:
        char_item = win.result_tree.topLevelItem(0)
        if char_item.childCount() > 0:
            win.result_tree.setCurrentItem(char_item.child(0))
            win._on_result_item(char_item.child(0), 0)

    # Populate sample price catalog
    win._price_catalog = {
        "gemini/gemini-2.5-flash": {"input_cost_per_token": 0.0000003, "output_cost_per_token": 0.0000025},
        "github_copilot/claude-haiku-4.5": {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
        "deepseek/deepseek-flash": {"input_cost_per_token": 0.00000014, "output_cost_per_token": 0.00000028},
    }
    win._chain_refresh_costs()

    win.statusBar().showMessage("Ready")

    # Desktop Suite: 1440 x 900
    win.resize(1440, 900)
    app.processEvents()

    # 01_PC_Books_Tab.png
    win.tabs.setCurrentIndex(1)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "01_PC_Books_Tab.png"))
    print("Captured 01_PC_Books_Tab.png")

    # 02_PC_Config_Tab.png (Settings Hub: Category 0 - AI Models & Chain)
    win.tabs.setCurrentIndex(0)
    win._set_config_category(0)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Tab.png"))
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Tab_Fold1_Top.png"))
    print("Captured 02_PC_Config_Tab.png (Category 0: AI Models & Chain)")

    # Category 1: API Credentials
    win._set_config_category(1)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Category_1_Credentials.png"))

    # Category 2: Performance & Limits (Provider limits table + Groq + Gemini Batch API)
    win._set_config_category(2)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Tab_Fold2_Bottom.png"))
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Category_2_Performance.png"))
    print("Captured 02_PC_Config_Tab_Fold2_Bottom.png (Category 2: Performance & Limits)")

    # Category 3: Cloud & Device Sync
    win._set_config_category(3)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Category_3_Sync.png"))

    # Category 4: General & Storage
    win._set_config_category(4)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_PC_Config_Category_4_General.png"))

    # Reset back to category 0
    win._set_config_category(0)
    app.processEvents()

    # 03_PC_Progress_Tab.png
    win.tabs.setCurrentIndex(2)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "03_PC_Progress_Tab.png"))
    print("Captured 03_PC_Progress_Tab.png")

    # 04_PC_Sync_Tab.png
    win.tabs.setCurrentIndex(3)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "04_PC_Sync_Tab.png"))
    print("Captured 04_PC_Sync_Tab.png")

    # 05_PC_Results_Tab.png
    win.tabs.setCurrentIndex(4)
    if hasattr(win, "result_splitter"):
        win.result_splitter.setSizes([380, 960])
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "05_PC_Results_Tab.png"))
    print("Captured 05_PC_Results_Tab.png")

    # Dark Mode Suite (Desktop 1440 x 900)
    win._set_theme(True)
    app.processEvents()

    # 06_PC_Dark_Mode_Books.png
    win.tabs.setCurrentIndex(1)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "06_PC_Dark_Mode_Books.png"))
    print("Captured 06_PC_Dark_Mode_Books.png")

    # 07_PC_Dark_Mode_Config.png
    win.tabs.setCurrentIndex(0)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "07_PC_Dark_Mode_Config.png"))
    print("Captured 07_PC_Dark_Mode_Config.png")

    # Switch back to Light Mode for Dialogs and Mobile Suite
    win._set_theme(False)
    app.processEvents()

    # 08_PC_Cost_Summary_Dialog.png
    sample_usage = {
        "claude/claude-3-5-sonnet": {"prompt": 142000, "completion": 38000, "chars": 420000},
        "deepseek/deepseek-chat": {"prompt": 85000, "completion": 24000, "chars": 260000},
        "openai/gpt-4o-mini": {"prompt": 110000, "completion": 31000, "chars": 340000},
    }
    cost_dlg = generator_gui.CostSummaryDialog(sample_usage, parent=win)
    mock_catalog = {
        "claude-3-5-sonnet": {"input_cost_per_token": 0.000003, "output_cost_per_token": 0.000015},
        "deepseek-chat": {"input_cost_per_token": 0.00000014, "output_cost_per_token": 0.00000028},
        "gpt-4o-mini": {"input_cost_per_token": 0.00000015, "output_cost_per_token": 0.00000060},
    }
    cost_dlg._on_prices_loaded(mock_catalog, "Sun, 13 Sep 2026 12:00:00 GMT")
    cost_dlg.show()
    app.processEvents()
    cost_dlg.grab().save(str(REVIEW_DIR / "08_PC_Cost_Summary_Dialog.png"))
    cost_dlg.close()
    print("Captured 08_PC_Cost_Summary_Dialog.png")

    # 09_PC_Setup_Wizard.png
    wizard = generator_gui.SetupWizard(parent=win)
    wizard.show()
    app.processEvents()
    wizard.grab().save(str(REVIEW_DIR / "09_PC_Setup_Wizard.png"))
    wizard.close()
    print("Captured 09_PC_Setup_Wizard.png")

    # 10_PC_WebDav_Folder_Dialog.png
    cfg = WebDavConfig(
        base_url="https://dav.example.com/remote.php/dav/files/USER/koreader/xray",
        username="testuser",
        password="secret",
    )
    orig_list_children = generator_gui.webdav_sync.list_children
    generator_gui.webdav_sync.list_children = lambda _cfg, _url: [
        ("https://dav.example.com/remote.php/dav/files/USER/koreader/xray/Fiction", "Fiction"),
        ("https://dav.example.com/remote.php/dav/files/USER/koreader/xray/SciFi", "SciFi"),
        ("https://dav.example.com/remote.php/dav/files/USER/koreader/xray/NonFiction", "NonFiction"),
    ]
    try:
        wd_dlg = generator_gui.WebDavFolderDialog(cfg, parent=win)
        wd_dlg.show()
        app.processEvents()
        wd_dlg.grab().save(str(REVIEW_DIR / "10_PC_WebDav_Folder_Dialog.png"))
        wd_dlg.close()
        print("Captured 10_PC_WebDav_Folder_Dialog.png")
    finally:
        generator_gui.webdav_sync.list_children = orig_list_children

    # Mobile / Compact Viewport Suite: 760 x 600 (Minimum responsive desktop/compact window)
    win.resize(760, 600)
    app.processEvents()

    # 01_Mobile_Books_Tab.png
    win.tabs.setCurrentIndex(1)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "01_Mobile_Books_Tab.png"))
    print("Captured 01_Mobile_Books_Tab.png")

    # 02_Mobile_Config_Tab.png (Settings Hub: Category 0 - AI Models & Chain)
    win.tabs.setCurrentIndex(0)
    win._set_config_category(0)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Tab.png"))
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Tab_Fold1_Top.png"))
    print("Captured 02_Mobile_Config_Tab.png (Category 0: AI Models & Chain)")

    # Category 1: API Credentials
    win._set_config_category(1)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Tab_Fold2_Mid.png"))
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Category_1_Credentials.png"))
    print("Captured 02_Mobile_Config_Tab_Fold2_Mid.png (Category 1: Credentials)")

    # Category 2: Performance & Limits (Provider limits table with Groq)
    win._set_config_category(2)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Tab_Fold3_Bottom.png"))
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Category_2_Performance.png"))
    print("Captured 02_Mobile_Config_Tab_Fold3_Bottom.png (Category 2: Performance & Limits)")

    # Category 3: Cloud & Device Sync
    win._set_config_category(3)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Category_3_Sync.png"))

    # Category 4: General & Storage
    win._set_config_category(4)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "02_Mobile_Config_Category_4_General.png"))

    # Reset back to category 0
    win._set_config_category(0)
    app.processEvents()

    # 03_Mobile_Progress_Tab.png
    win.tabs.setCurrentIndex(2)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "03_Mobile_Progress_Tab.png"))
    print("Captured 03_Mobile_Progress_Tab.png")

    # 04_Mobile_Sync_Tab.png
    win.tabs.setCurrentIndex(3)
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "04_Mobile_Sync_Tab.png"))
    print("Captured 04_Mobile_Sync_Tab.png")

    # 05_Mobile_Results_Tab.png
    win.tabs.setCurrentIndex(4)
    if hasattr(win, "result_splitter"):
        win.result_splitter.setSizes([360, 360])
    app.processEvents()
    win.grab().save(str(REVIEW_DIR / "05_Mobile_Results_Tab.png"))
    print("Captured 05_Mobile_Results_Tab.png")

    win.close()
    print("All screenshots successfully captured into:", REVIEW_DIR)

    # Generate prompt.txt for independent reviewer sub-agent
    prompt_text = f"""# X-Ray Generator Desktop Application — Independent Sub-Agent UI/UX Design System Audit

## Target & Scope
You are an independent Principal Design System Architect & Staff Consumer UX Reviewer (Google MD3 / Material You / Modern Desktop Standards).
Your objective in this session is to conduct an uncompromising, pixel-level UI/UX review of the **X-Ray Generator GUI** desktop application across **both PC Desktop (1440 × 900) and Compact Responsive (760 × 600)** viewports, as well as **Light and Dark Material Design 3 Themes**, and all **Modal Dialogs**.

This review operates within an autonomous iterative loop. If ANY visual defect, design system inconsistency, typography flaw, or touch target issue is found, you must output a structured, actionable defect punch-list with exact file references and proposed code diffs so the engineering agent can implement fixes, re-build, re-capture screenshots, and re-present for sign-off until a 100% PASS is achieved.

---

## MANDATORY ANTI-RUBBERSTAMPING CHECK: TOP 12 VISUAL DISQUALIFIERS

> [!CAUTION]
> **DO NOT RUBBER-STAMP OR DEFAULT TO HIGH SCORES.**
> In prior iterations, subagents committed critical sycophancy errors by skimming surfaces and ignoring thick black rendering lines, dark inverted un-themed logo blocks, collapsed 0px spinboxes with invisible numbers, squashed multi-widget table cells, truncated ellipsis data, sliced containers, and 600px-wide stretched spinboxes.
> You MUST inspect every image at 100% zoom and explicitly certify the absence of each of the following **Top 12 Visual Disqualifiers** in Section 2 of your report.
> If **ANY** of these 12 flaws exists in ANY screenshot, your overall score is **STRICTLY CAPPED AT <= 70 / 100** and your verdict MUST be **`### VERDICT: ITERATE_REQUIRED`**.

1. **Solid Black Rendering Glitches & Dark Inverted Block Artifacts**: Black horizontal/vertical artifact lines running across table rows, or solid black circles/squares/blocks (`#000000`, `#24292f`) on light surfaces caused by unstyled fallback icons, widget clipping, or CSS overflow.
2. **Cell Widget Height Collisions**: Table row default section height less than the embedded widget height + vertical padding (`rowHeight < widgetHeight + 8dp`), causing cell borders to overlap combo/input text.
3. **Invisible / Collapsed Inner Input Values**: Numbers/text inside spinboxes, double spinboxes, or line edits collapsed to 0px width (rendering only up/down arrows or blank boxes with missing values) due to insufficient column width or competing internal paddings.
4. **Multi-Widget Cell Collisions & Overlap**: Multiple widgets (e.g. action button + badge label) crammed into a narrow cell causing overlap, squashed button text, or truncated badge glyphs.
5. **Data Column Ellipsis Truncation**: Table text or numeric data (e.g. `$0.30/...` or "Google Gemi...") truncated to ellipsis due to narrow columns or excessive cell item padding.
6. **Truncated Combobox Labels**: Provider or model combobox text clipped (e.g. "Google Gemi..." instead of "Google Gemini") or covered by the dropdown arrow icon due to insufficient column width.
7. **Absurdly Stretched Inputs**: Single-digit numbers, numeric spinboxes, or short strings stretched across full-width layouts (> 200px wide without a reasonable `maximumWidth`).
8. **Abrupt Container / Mid-Control Slicing**: Cards, group boxes, or input fields sliced halfway through at a viewport fold without visual separation or clearance from the action bar.
9. **Action Bar / Sticky Dock Collisions**: Bottom action bars floating awkwardly over scrollable content with mismatched corner radii touching window edges, or obscuring interactive controls.
10. **Unpainted Cell Gaps / Grid Holes**: Table columns showing white, transparent, or unpainted gaps between cells.
11. **Above-the-Fold Blindness**: Reviewing only Fold 1 of a scrollable panel while ignoring below-the-fold controls (`_Fold2_Mid.png`, `_Fold3_Bottom.png`).
12. **Concentric Radii Violations & Chart Label Collisions**: Inner elements having larger corner radius than outer parent cards ($R_{{\\text{{inner}}}} > R_{{\\text{{outer}}}} - \\text{{padding}}$), or overlapping numbers/glyphs.

---

## Review Artifacts & Environment
- **Target Viewports**:
  - **PC Desktop Viewport**: `1440 × 900` (High-DPI Display Scaling)
  - **Compact Viewport**: `760 × 600` (Minimum Responsive Desktop / Tablet Mode)
- **Screenshot Directory**: `{REVIEW_DIR}`
- **Application Codebase**: `C:\\Users\\-GALACTUS-\\Documents\\GitHub\\KOReader\\xray.koplugin`
- **Application Entrypoint**: `generator_gui.py` & Theme Engine: `md3_theme.py`

---

## Screenshot Inventory to Audit

Inspect all screenshot files matching `*_PC_*.png` and `*_Mobile_*.png` dynamically discovered in the screenshot directory:

### Part A: PC Desktop Viewport Suite (`1440 × 900`)
| Screenshot | Screen / Mode | Description |
|:---|:---|:---|
| `01_PC_Books_Tab.png` | Books Tab (Light) | Calibre library table, search filter, single-row streamlined top toolbar and bottom action bar |
| `02_PC_Config_Tab.png` / `02_PC_Config_Tab_Fold1_Top.png` | Settings Hub (Light, Category 0) | Category 0: AI Models & Fallback Chain (52px rows, unclipped comboboxes, Check balance button) |
| `02_PC_Config_Tab_Fold2_Bottom.png` | Settings Hub (Light, Category 2) | Category 2: Performance & Limits table with all 6 providers including Groq, Gemini Batch API |
| `02_PC_Config_Category_1_Credentials.png` | Settings Hub (Light, Category 1) | Category 1: Cloud API keys, OpenAI-compatible endpoint, GitHub Copilot |
| `02_PC_Config_Category_2_Performance.png` | Settings Hub (Light, Category 2) | Category 2: Provider concurrency & chunk limits table with Groq, Gemini Batch API |
| `02_PC_Config_Category_3_Sync.png` | Settings Hub (Light, Category 3) | Category 3: KOReader device IP/port and WebDAV cloud configuration |
| `02_PC_Config_Category_4_General.png` | Settings Hub (Light, Category 4) | Category 4: Calibre library detection, X-Ray output directory, language |
| `03_PC_Progress_Tab.png` | Progress Tab (Light) | Dual linear progress bars, live stats chips, log viewer console |
| `04_PC_Sync_Tab.png` | Sync Center (Light) | High-utility Transfer Center with Wi-Fi direct push, WebDAV cloud sync, Settings link |
| `05_PC_Results_Tab.png` | Results Tab (Light) | Split tree view of characters/locations and detailed entity viewer |
| `06_PC_Dark_Mode_Books.png` | Books Tab (Dark) | MD3 dark tonal surface stack, luminance transitions, high-contrast badges |
| `07_PC_Dark_Mode_Config.png` | Config Hub (Dark) | MD3 dark tonal stack for Settings Hub, pill buttons, forms, and tables |
| `08_PC_Cost_Summary_Dialog.png` | Cost Summary Modal | 28px rounded container, tabular token usage table, filled action button |
| `09_PC_Setup_Wizard.png` | Setup Wizard Dialog | Step progression wizard, filled next/finish buttons, outlined back button |
| `10_PC_WebDav_Folder_Dialog.png` | WebDAV Folder Modal | Remote folder tree browser, filled selection button |

### Part B: Mobile / Compact Responsive Suite (`760 × 600`)
| Screenshot | Screen / Mode | Description |
|:---|:---|:---|
| `01_Mobile_Books_Tab.png` | Books Tab (Compact) | Compact responsive layout of library table and action row |
| `02_Mobile_Config_Tab_Fold1_Top.png` | Config Hub (Compact, Category 0) | Category 0: Fallback chain table (52px rows, unclipped comboboxes, no black lines) |
| `02_Mobile_Config_Tab_Fold2_Mid.png` | Config Hub (Compact, Category 1) | Category 1: Cloud API keys, OpenAI-compatible endpoint, GitHub Copilot |
| `02_Mobile_Config_Tab_Fold3_Bottom.png` | Config Hub (Compact, Category 2) | Category 2: Provider concurrency & chunk limits table with Groq, Gemini Batch API |
| `03_Mobile_Progress_Tab.png` | Progress Tab (Compact) | Compact progress view with stat chips and log console |
| `04_Mobile_Sync_Tab.png` | Sync Center (Compact) | Compact Transfer Center with Wi-Fi direct push and WebDAV cloud storage cards |
| `05_Mobile_Results_Tab.png` | Results Tab (Compact) | Responsive split view with entity inspector |

---

## Core Design System Evaluation Criteria

### 1. Tonal Surface Stack & Elevation Hierarchy
- Visual depth must be established through luminance transitions (`surfaceContainerLowest` to `surfaceContainerHighest`).
- Strict elimination of harsh, high-contrast 1px border lines as the primary element separator.
- Authentic Google Material Design 3 tonal color roles applied consistently across Light and Dark modes.

### 2. Concentric Nested Radii Math
- Outer containers must dictate the curvature of inner elements:
  $$R_{{\\text{{inner}}}} = \\max(0, R_{{\\text{{outer}}}} - \\text{{padding}})$$
- Reject optical corner distortion, right-angled elements inside rounded parents, or mismatched radii.
- Full capsule action pills and chips must enforce rounded geometry (18-20px+).

### 3. Sheets & Modals Hierarchy
- Modals & dialogs (`CostSummaryDialog`, `SetupWizard`, `WebDavFolderDialog`) must enforce 28px rounded corners (`shape.corner.extra-large`), elevated tonal surface containers, and clear action button rows.
- Sticky action bars must stay anchored with subtle tonal borders, zero screen-edge clipping, and clean flush docking.

### 4. Data Density, Ergonomics & Tabular Numerals
- Table rows must adhere strictly to ergonomic heights (44-56dp standard).
- All numbers, token counts, timestamps, and counters must use monospace / tabular font settings to eliminate layout jitter.
- Right-aligned amounts, clear progress indicators.

### 5. Data Visualization & Chart Readability
- Progress indicators must follow MD3 Linear Progress Indicator guidelines (rounded ends, primary fill on surfaceContainerHighest track).
- High-contrast text on colored badges (> 7:1 contrast ratio).
- Elevated tooltips with rounded corners (8-12px).

### 6. Temporal & Calendar Alignment
- Accurate dates, timestamps, and status indicators.

### 7. Mobile / Compact Responsiveness & Touch Ergonomics
- Fluid layout without clipping or horizontal overflow.
- Touch targets must meet minimum 40-48dp height (with compact chips at 32dp).
- Primary tabs must provide comfortable hit areas and active pill indicator styling.

---

## Required Report Output Format

You must output your evaluation strictly using the following markdown sections:

```markdown
# X-Ray Generator GUI — UI/UX Independent Sub-Agent Audit Report

## 1. Executive Scorecard
| Dimension | Rating (out of 10) | Compliance Assessment |
|:---|:---:|:---|
| 1. Tonal Surface Stack & Color Fidelity | X / 10 | ... |
| 2. Concentric Shape & Nested Radii Math | X / 10 | ... |
| 3. Sheets & Modals Hierarchy | X / 10 | ... |
| 4. Data Density & Tabular Numerals | X / 10 | ... |
| 5. Chart Readability & Accessibility | X / 10 | ... |
| 6. Temporal & Calendar Alignment | X / 10 | ... |
| 7. Mobile Responsiveness & Touch Ergonomics | X / 10 | ... |
| **Overall Platform Grade** | **XX / 100** | ... |

## 2. Negative-Proof Disqualifiers Verification
(Reviewer MUST explicitly certify every item as PASS or FAIL)
| # | Disqualifier Check | Status (PASS / FAIL) | Photographic Evidence / Observation |
|---|:---|:---:|:---|
| 1 | Zero Solid Black Glitches & Dark Inverted Block Artifacts | PASS / FAIL | ... |
| 2 | Table Row Clearance >= Embedded Widget Height | PASS / FAIL | ... |
| 3 | Input Numbers Visible & Uncollapsed (No 0px Spinbox Values) | PASS / FAIL | ... |
| 4 | Zero Multi-Widget Cell Collisions or Squashed Buttons | PASS / FAIL | ... |
| 5 | Table Data & Prices Unclipped (Zero Ellipsis `...`) | PASS / FAIL | ... |
| 6 | Combobox Text Unclipped by Dropdown Arrows | PASS / FAIL | ... |
| 7 | Input Controls Constrained (No Stretched Spinboxes) | PASS / FAIL | ... |
| 8 | Clean Viewport Fold Continuity (No Mid-Input Slicing) | PASS / FAIL | ... |
| 9 | Docked Action Bar Flush & Non-Occluding | PASS / FAIL | ... |
| 10 | Zero Unpainted Table Cell Gaps | PASS / FAIL | ... |
| 11 | Complete Multi-Fold Inspection (Folds 1, 2, 3) | PASS / FAIL | ... |
| 12 | Concentric Radii Math Respected & Zero Glyph Collisions | PASS / FAIL | ... |

## 3. Defect & Inconsistency Punch-List
(If any defects exist, populate the table below. If zero defects, state "Zero defects identified".)
| ID | Viewport | Screenshot # | File Path & Lines | Visual Defect | Design Violation | Severity | Proposed Code Remediation |

## 4. Detailed Technical Analysis
(Breakdown across key pages / components)

## 5. Automated Verdict
(MUST be exactly one of the following two lines)
### VERDICT: ITERATE_REQUIRED
or
### VERDICT: SIGN-OFF_APPROVED
```
"""
    prompt_file = REVIEW_DIR / "prompt.txt"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    print("Wrote audit specification to:", prompt_file)


if __name__ == "__main__":
    capture_all()

