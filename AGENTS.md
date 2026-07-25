# AGENTS.md

Guidance for AI coding agents working in `xray.koplugin`. Read this before touching any file.

## What this plugin is

A KOReader plugin that brings an Amazon Kindle–style **X-Ray** feature to EPUB books: it uses an
LLM to analyze the text a reader has *already read* (zero spoilers) and produces characters,
locations, themes and a timeline, stored in `xray_data.json`.

X-Ray data is expected to be produced off-device and then synced to KOReader via cache/sync flows.
This plugin focuses on rendering, filtering by reading progress, notes, and transfer.

## Data contract expectations

`xray_data.json` is consumed by this plugin with these expectations:
- character `events` carry `{event, xref, anchor}`
- `timeline` entries carry `{sequence, event, character, xref, anchor}`
- `descriptions` are progressive `[{percent, text}]` entries
- progressive visibility relies on cache/progress behavior in `cachemanager.lua` and `main.lua`

## File map

### Device side (Lua, runs in KOReader)
- `main.lua` — plugin UI: menu, X-Ray viewer, text-selection handler, entity visibility.
- `cachemanager.lua` — progressive `xray_data.json` cache (loads only ≤ current reading %).
- `characternotes.lua` — per-character user notes.
- `sync.lua` — WebDAV upload/download of X-Ray data.
- `xray_receiver.lua` — tiny LuaSocket HTTP server that accepts `xray_data.json` pushed from a PC.
- `localization_xray.lua`, `languages/` — i18n strings.
- `_meta.lua` — plugin metadata (name/version/description).

## i18n rule

All user-visible strings **must** live in `languages/*.po` files.
`localization_xray.lua` loads translations from those `.po` files at runtime via `self.loc:t(key)`.
The `.po` file is the single source of truth — `localization_xray.lua` only keeps a small
hardcoded fallback table for strings that might be needed before the `.po` is loaded.

- Never hardcode a translated string in `main.lua` or other Lua files.
- When you add a new user-visible string, add it to `languages/zh.po` first, then reference it
  with `self.loc:t("your_key")` in the Lua code.
- The fallback table in `localization_xray.lua` should be updated too, but the `.po` file is
  canonical.

### GUI i18n (Python tooling, `generator_gui.py`)

The PyQt6 desktop GUI has its **own** i18n system, separate from the Lua `.po` files above
(different runtime, different strings). It lives in `gui_i18n.py`.

Pattern — **English source strings are the keys**:

- Every user-visible string in `generator_gui.py` is wrapped in `tr(...)`, imported from
  `gui_i18n`. The English text *is* the lookup key, so the code stays readable and grep-able:
  `QPushButton(tr("Start Analysis"))`.
- **Interpolate with `str.format`, never f-strings**, so the key stays stable regardless of the
  runtime value: `tr("{n} books").format(n=len(rows))` — the key is `"{n} books"`.
- All translations live in one registry in `gui_i18n.py`: `_TRANSLATIONS`, shaped as
  `{lang_code: {english_source: translated}}`. English is the source language and has no dict
  (keys fall through to themselves). A missing key falls back to the English source, so the UI
  never breaks on an untranslated string.
- Language is resolved (highest priority first) from an explicit `set_language()` call, the
  `XRAY_GUI_LANG` env var, then the system locale. Chinese variants collapse to `zh`
  (Simplified) or `zh_TW` (Traditional). The GUI persists the user's choice in preferences
  (`gui_lang`) and applies it on restart.

To **add a new UI string**:
1. Write it in English inside `tr("…")` at the call site in `generator_gui.py`.
2. Add the same English text as a key to each language dict in `gui_i18n.py::_TRANSLATIONS`.

To **add a new language**:
1. Add `("code", "Native Name")` to `gui_i18n.py::AVAILABLE_LANGUAGES`.
2. Add a `"code": { … }` dict to `_TRANSLATIONS`.

Rules:
- Never hardcode a user-visible English string in `generator_gui.py` without `tr(...)`.
- Never use f-strings for translatable text — always `tr("…{name}…").format(name=…)`.
- Keep `gui_i18n.py` (Python GUI) and the `languages/*.po` files (Lua plugin) as **separate**
  systems; do not try to share keys between them.

### PC side (Python batch generator)
- `generator.py` — entry point / orchestration for off-device generation.
  Exposes optional GUI hooks: `set_gui_hooks(progress_hook, fatal_raises)`, `FatalChunkError`,
  and `_fatal_stop()` (used instead of `os._exit(1)` in the chunk worker). These are **inert in
  CLI mode** (default hard-exit / `[PROGRESS]` prints unchanged); only `generator_gui.py` sets them.
- `generator_gui.py` — **PyQt6 desktop front-end** for `generator.py` (optional). Thin UI layer
  only: it reuses the backend functions (`process_book`, `create_client`, `scan_calibre_library`,
  `push_to_koreader`, …) and adds no pipeline logic, so the PARITY RULE does not apply to it.
  Run with `python generator_gui.py`. Requires `pip install PyQt6` (see `requirements.txt`).
- `gui_i18n.py` — i18n for the PyQt6 GUI (`tr()` + `_TRANSLATIONS` registry; see the GUI i18n
  section above). GUI-only; unrelated to the Lua `languages/*.po` files.
- `master_data.py` — accumulate/merge/dedup/serialize (mirror of Lua `MasterData`).
- `text_utils.py` — stateless helpers, `META_THEMES`, name normalization.
- `ai_client.py` — multi-provider client, retry, caching, model discovery.
- `epub_reader.py` — EPUB parsing, `get_sdr_name`.
- `webdav_sync.py` — WebDAV upload/download/status for the GUI (stdlib only). Mirrors the
  on-device `sync.lua` remote layout `<base>/<sdr_name>/xray_analysis/xray_data.json`, so data
  pushed from the PC is picked up by KOReader's X-Ray *Cloud Sync* and vice-versa.
- `calibre_browser.py`, `master_data.py`, `xray_web_monitor.py` (FastAPI progress dashboard).
- `.env` (from `.env.example`) — `XRAY_API_BASE`, `XRAY_API_KEY`, `XRAY_MODEL`, `CALIBRE_LIBRARY`.

### Shared
- `prompts/zh.json` — single source of truth for all prompts (see above).

## Build / test / lint

- **No build step** for Lua — files run directly in KOReader.
- **Lua syntax/lint**: use `luacheck` on plugin files (see repo tasks).
- **Python**: `python generator.py book.epub` (single book) or `--browse` (Calibre library).
  Requires `pip install openai opencc-python-reimplemented python-dotenv` (`anthropic` optional).
- **GUI (optional)**: `python generator_gui.py` (`pip install PyQt6`, or `requirements.txt`).
  It drives the same backend as the CLI; the CLI (`python generator.py …`) is unaffected by it.
- **GUI i18n check**: `python check_i18n.py` — verifies every `tr()` key exists in all
  language dicts in `gui_i18n.py`, and flags f-strings in translatable contexts.
  **Run this whenever `generator_gui.py` or `gui_i18n.py` is touched.**
- **Sync test**: generate data with Python tooling, push/sync into KOReader, then confirm
  `xray_data.json` is loaded and visible as expected in plugin UI.

## Change checklist for agents

When editing data ingestion/rendering in this plugin:

1. Make the change on one side.
2. Keep the output contract (fields, ordering, caps) unchanged unless the task explicitly
   requires it — and if it changes, update `cachemanager.lua`/`main.lua` consumers too.

When editing Python generator tooling (`generator.py`, `generator_gui.py`, `gui_i18n.py`):

3. **Run `python check_i18n.py` and fix all errors before committing.**
   This script uses AST-based analysis to verify every `tr("key")` call has a
   matching entry in every language dict in `gui_i18n.py`, and flags f-strings
   passed to Qt text methods without `tr()`. Exit 0 = clean.
