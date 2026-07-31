#!/usr/bin/env python3
"""
check_i18n.py — Audit i18n coverage for xray.koplugin.

Checks
------
Python GUI (generator_gui.py):

1. Every  tr("key")  call in generator_gui.py has a translation entry in
   *every* language dict in gui_i18n._TRANSLATIONS.

2. f-strings passed as the *direct* argument to tr() — use
   tr("…{name}…").format(name=…) instead.

3. f-strings passed directly to common Qt text-setter methods or constructors
   without tr().  Suppress intentional ones with  # noqa: i18n .

Lua files (*.lua in this directory):

4. Every  self.loc:t("key")  call in a Lua file has a msgid entry in every
   languages/*.po file.  A missing key means the string shows as an empty
   string or raw key for non-English users.

5. String literals containing non-ASCII characters (e.g. "✓ ", "☐ ") that
   are directly concatenated (via ..) with a self.loc:t() call — these are
   partially-translated strings.  The whole visible string must come from a
   single self.loc:t() call.  Suppress intentional ones with  -- noqa: i18n .

Run from the xray.koplugin directory:
    python check_i18n.py
Exit 0 = clean, 1 = violations found.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
GUI_FILE   = SCRIPT_DIR / "generator_gui.py"
I18N_FILE  = SCRIPT_DIR / "gui_i18n.py"

# Qt/PyQt methods that accept user-visible text as first positional argument.
_UI_METHODS = {
    "setText", "setWindowTitle", "setTitle", "setPlaceholderText",
    "setToolTip", "setStatusTip", "showMessage",
}
# Qt constructors whose first argument is a visible label string.
_UI_CONSTRUCTORS = {"QLabel", "QPushButton", "QCheckBox", "QGroupBox", "QAction"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_translations() -> dict[str, dict[str, str]]:
    sys.path.insert(0, str(SCRIPT_DIR))
    import gui_i18n  # type: ignore[import]
    return gui_i18n._TRANSLATIONS  # noqa: SLF001


def _source() -> str:
    return GUI_FILE.read_text(encoding="utf-8")


def _lineno(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


# ---------------------------------------------------------------------------
# Check 1 — tr() keys missing from a language dict
# ---------------------------------------------------------------------------

class _TrKeyVisitor(ast.NodeVisitor):
    """Collect (lineno, decoded_key) for every tr("literal") call."""

    def __init__(self) -> None:
        self.keys: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "tr"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self.keys.append((node.lineno, node.args[0].value))
        self.generic_visit(node)


def _check_missing_translations(
    source: str,
    translations: dict[str, dict[str, str]],
) -> tuple[list[str], int]:
    """Return (error_lines, unique_key_count)."""
    tree = ast.parse(source, filename=str(GUI_FILE))
    visitor = _TrKeyVisitor()
    visitor.visit(tree)

    errors: list[str] = []
    languages = list(translations.keys())
    seen: set[str] = set()

    for lineno, key in visitor.keys:
        if key in seen:
            continue
        seen.add(key)
        for lang in languages:
            if key not in translations[lang]:
                errors.append(f"  L{lineno}: tr({key!r}) not in '{lang}'")

    return errors, len(seen)


# ---------------------------------------------------------------------------
# Check 2 — f-strings inside tr(f"…")
# ---------------------------------------------------------------------------
_TR_FSTRING_RE = re.compile(r"""\btr\(\s*f['"]""")


def _check_fstrings_in_tr(source: str) -> list[str]:
    errors: list[str] = []
    for m in _TR_FSTRING_RE.finditer(source):
        if "# noqa: i18n" in source[m.start(): source.find("\n", m.start())]:
            continue
        lineno = _lineno(source, m.start())
        snippet = source[m.start(): m.start() + 80].replace("\n", " ")
        errors.append(
            f"  L{lineno}: f-string inside tr() — use tr('…{{x}}…').format(x=…): {snippet!r}"
        )
    return errors


# ---------------------------------------------------------------------------
# Check 3 — f-strings passed directly to Qt text methods / constructors
# ---------------------------------------------------------------------------
_UI_FSTRING_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(m) + r"\s*\(\s*f['\"]" for m in sorted(_UI_METHODS))
    + "|"
    + "|".join(re.escape(c) + r"\s*\(\s*f['\"]" for c in sorted(_UI_CONSTRUCTORS))
    + r")",
    re.MULTILINE,
)


def _check_ui_fstrings(source: str) -> list[str]:
    warnings: list[str] = []
    for m in _UI_FSTRING_RE.finditer(source):
        line_end = source.find("\n", m.start())
        line = source[m.start(): line_end if line_end != -1 else m.start() + 120]
        if "# noqa: i18n" in line:
            continue
        lineno = _lineno(source, m.start())
        snippet = line.strip()[:100]
        warnings.append(
            f"  L{lineno}: f-string passed to UI method without tr() — review: {snippet!r}"
        )
    return warnings


# ---------------------------------------------------------------------------
# Lua helpers — parse languages/*.po msgids
# ---------------------------------------------------------------------------
_MSGID_RE     = re.compile(r'^msgid\s+"((?:[^"\\]|\\.)*)"')
_MSGID_CONT_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"')


def _parse_po_msgids(po_path: Path) -> set[str]:
    msgids: set[str] = set()
    lines = po_path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = _MSGID_RE.match(lines[i])
        if m:
            value = m.group(1)
            j = i + 1
            while j < len(lines):
                cm = _MSGID_CONT_RE.match(lines[j])
                if cm:
                    value += cm.group(1)
                    j += 1
                else:
                    break
            if value:
                msgids.add(value.replace('\\"', '"').replace("\\'", "'"))
            i = j
        else:
            i += 1
    return msgids


# ---------------------------------------------------------------------------
# Check 4 — self.loc:t("key") keys missing from a languages/*.po file
# ---------------------------------------------------------------------------
_LOC_T_RE = re.compile(r'\bself\.loc:t\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _check_lua_key_coverage(lua_files: list[Path], po_files: list[Path]) -> list[str]:
    """Return error lines for any self.loc:t("key") not present in a .po file."""
    # Collect all keys from Lua files
    lua_keys: dict[str, str] = {}  # key -> "file:lineno"
    for lua_file in lua_files:
        text = lua_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _LOC_T_RE.finditer(line):
                key = m.group(1).replace('\\"', '"').replace("\\'", "'")
                lua_keys.setdefault(key, f"{lua_file.name}:{lineno}")

    errors: list[str] = []
    for po_path in po_files:
        lang = po_path.stem if po_path.parent == SCRIPT_DIR else po_path.parent.name
        # For languages/en.po etc. the "lang" is the stem of the file
        lang = po_path.stem
        po_ids = _parse_po_msgids(po_path)
        for key, loc in sorted(lua_keys.items(), key=lambda x: x[1]):
            if key not in po_ids:
                errors.append(f"  {loc}: self.loc:t({key!r}) missing from {po_path.name}")
    return errors


# ---------------------------------------------------------------------------
# Check 5 — non-ASCII string literals concatenated with self.loc:t()
#
# Flags patterns like:  "✓ " .. self.loc:t(...)
#                   or: self.loc:t(...) .. " ✓"
# where the literal contains at least one non-ASCII character (codepoint > 127).
# These are partially-translated strings; the whole visible string must come
# from a single self.loc:t() call.
# Suppress intentional ones with  -- noqa: i18n  on the same line.
# ---------------------------------------------------------------------------
_LOC_T_CONCAT_BEFORE_RE = re.compile(
    r'"((?:[^"\\]|\\.)*[^\x00-\x7F](?:[^"\\]|\\.)*)"\s*\.\.\s*self\.loc:t\('
)
_LOC_T_CONCAT_AFTER_RE = re.compile(
    r'self\.loc:t\([^)]*\)\s*\.\.\s*"((?:[^"\\]|\\.)*[^\x00-\x7F](?:[^"\\]|\\.)*)"'
)


def _check_lua_partial_translations(lua_files: list[Path]) -> list[str]:
    errors: list[str] = []
    for lua_file in lua_files:
        text = lua_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            if "-- noqa: i18n" in line:
                continue
            for pat in (_LOC_T_CONCAT_BEFORE_RE, _LOC_T_CONCAT_AFTER_RE):
                if pat.search(line):
                    snippet = line.strip()[:100]
                    errors.append(
                        f"  {lua_file.name}:{lineno}: non-ASCII literal concatenated with "
                        f"self.loc:t() — use a dedicated translation key: {snippet!r}"
                    )
                    break
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # --- Python GUI checks ---
    py_errors: list[str] = []
    py_warnings: list[str] = []
    py_tr_keys = 0

    if GUI_FILE.exists():
        source = _source()
        translations = _load_translations()
        languages = list(translations.keys())
        e1, py_tr_keys = _check_missing_translations(source, translations)
        e2 = _check_fstrings_in_tr(source)
        e3 = _check_ui_fstrings(source)
        py_errors = e1 + e2
        py_warnings = e3

    # --- Lua checks ---
    lua_files = sorted(SCRIPT_DIR.glob("*.lua"))
    po_files  = sorted((SCRIPT_DIR / "languages").glob("*.po"))

    lua_errors: list[str] = []
    if lua_files and po_files:
        lua_errors += _check_lua_key_coverage(lua_files, po_files)
        lua_errors += _check_lua_partial_translations(lua_files)

    # --- Report ---
    all_errors = py_errors + lua_errors
    all_warnings = py_warnings

    if py_errors:
        print(f"check_i18n: {len(py_errors)} error(s) in {GUI_FILE.name}:\n")
        for e in py_errors:
            print(e)

    if lua_errors:
        if py_errors:
            print()
        print(f"check_i18n: {len(lua_errors)} Lua i18n error(s):\n")
        for e in lua_errors:
            print(e)

    if all_warnings:
        if all_errors:
            print()
        print(f"check_i18n: {len(all_warnings)} warning(s) (f-strings in UI calls — may be intentional):\n")
        for w in all_warnings:
            print(w)

    if not all_errors and not all_warnings:
        msg_parts = []
        if GUI_FILE.exists():
            msg_parts.append(f"{py_tr_keys} tr() keys × {len(list(translations.keys()))} languages")
        if lua_files:
            msg_parts.append(f"{len(lua_files)} Lua file(s) × {len(po_files)} .po file(s)")
        print("check_i18n: OK — " + ", ".join(msg_parts) + ", no violations.")
        return 0

    if all_errors:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())


