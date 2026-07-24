#!/usr/bin/env python3
"""
check_i18n.py — Audit GUI i18n coverage for generator_gui.py.

Checks
------
1. Every  tr("key")  call in generator_gui.py has a translation entry in
   *every* language dict in gui_i18n._TRANSLATIONS.
   Missing keys = strings that will fall back to English for non-English users.

2. f-strings passed as the *direct* argument to tr() — these violate the
   AGENTS.md rule "never use f-strings for translatable text".
   Use  tr("…{name}…").format(name=…)  instead.

3. f-strings passed directly to common Qt text-setter methods or constructors
   without being wrapped in tr() first.  These are likely untranslated.
   (Some false positives are possible for purely numeric/technical strings;
   review the output and suppress intentional ones with a  # noqa: i18n  comment.)

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
#
# Uses the AST so Python handles all escape sequences (\n, \u2026, …)
# exactly as the runtime would — making key comparison with gui_i18n reliable.
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
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    source = _source()
    translations = _load_translations()
    languages = list(translations.keys())

    e1, tr_keys_scanned = _check_missing_translations(source, translations)
    e2 = _check_fstrings_in_tr(source)
    e3 = _check_ui_fstrings(source)

    errors   = e1 + e2
    warnings = e3

    if errors:
        print(f"check_i18n: {len(errors)} error(s) in {GUI_FILE.name}:\n")
        for e in errors:
            print(e)

    if warnings:
        if errors:
            print()
        print(f"check_i18n: {len(warnings)} warning(s) (f-strings in UI calls — may be intentional):\n")
        for w in warnings:
            print(w)

    if not errors and not warnings:
        print(
            f"check_i18n: OK — {tr_keys_scanned} tr() keys × {len(languages)} languages, "
            "no violations."
        )
        return 0

    if errors:
        return 1

    # Warnings only — exit 0 but still print them so the agent notices.
    return 0


if __name__ == "__main__":
    sys.exit(main())
