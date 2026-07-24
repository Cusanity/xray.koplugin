"""
Calibre library browser for X-Ray Generator.

Handles scanning, browsing, and selecting books from a Calibre library.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from typing import Any

from epub_reader import parse_metadata_opf
from text_utils import XML_NS_OPF

# =============================================================================
# Preferences Persistence
# =============================================================================

_PREFS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".xray_prefs.json"
)


def _load_preferences() -> dict[str, Any]:
    """Load preferences from JSON file."""
    if os.path.exists(_PREFS_FILE):
        try:
            with open(_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_preferences(prefs: dict[str, Any]) -> None:
    """Save preferences to JSON file."""
    try:
        with open(_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Silent fail - preferences are not critical


# =============================================================================
# Calibre Library Detection
# =============================================================================


def find_calibre_libraries() -> list[str]:
    """Return a list of valid Calibre library paths found on this machine.

    Checks (in order):
    1. Calibre's own ``library_usage_stats.json`` config file (most reliable).
    2. Common default folder locations across Windows / macOS / Linux.

    Returns a deduplicated list of paths that contain a ``metadata.db`` file.
    """
    candidates: list[str] = []

    # --- 1. Read from Calibre's own configuration ---
    calibre_cfg_dirs: list[str] = []
    home = os.path.expanduser("~")
    if os.name == "nt":  # Windows
        app_data = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        calibre_cfg_dirs.append(os.path.join(app_data, "calibre"))
    else:  # macOS / Linux
        calibre_cfg_dirs.append(os.path.join(home, ".config", "calibre"))
        calibre_cfg_dirs.append(
            os.path.join(home, "Library", "Preferences", "calibre")
        )

    for cfg_dir in calibre_cfg_dirs:
        stats_file = os.path.join(cfg_dir, "library_usage_stats.json")
        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Keys are library paths; sort by usage count (value) descending.
                if isinstance(data, dict):
                    for path in sorted(data, key=lambda p: data[p], reverse=True):
                        candidates.append(os.path.normpath(path))
            except (json.JSONDecodeError, OSError, TypeError):
                pass

    # --- 2. Common default locations ---
    common: list[str] = [
        os.path.join(home, "Calibre Library"),
        os.path.join(home, "Documents", "Calibre Library"),
        os.path.join(home, "Desktop", "Calibre Library"),
        os.path.join(home, "calibre-library"),
    ]
    if os.name == "nt":
        # Also try the drive root (some users put it at D:\Calibre Library etc.)
        for drive in ["C", "D", "E"]:
            common.append(os.path.join(f"{drive}:\\", "Calibre Library"))
    candidates.extend(common)

    # --- Deduplicate and validate ---
    seen: set[str] = set()
    result: list[str] = []
    for p in candidates:
        key = p.lower() if os.name == "nt" else p
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(os.path.join(p, "metadata.db")):
            result.append(p)
    return result


# =============================================================================
# Calibre Library Scanning
# =============================================================================


def scan_calibre_library(library_path: str) -> list[dict[str, str]]:
    """Scan Calibre library using metadata.db to list only books registered in Calibre.

    Queries Calibre's SQLite database directly for accurate results.
    """
    books = []

    if not os.path.isdir(library_path):
        print(f"Error: Calibre library not found at {library_path}")
        return books

    db_path = os.path.join(library_path, "metadata.db")
    if not os.path.exists(db_path):
        print(f"Error: Calibre metadata.db not found at {db_path}")
        print("  Is this a valid Calibre library?")
        return books

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT b.title,
                   GROUP_CONCAT(a.name, ' & ') AS author,
                   b.timestamp                  AS added_date,
                   b.path                       AS book_dir,
                   d.name                       AS epub_stem
            FROM books b
            JOIN data d ON d.book = b.id AND UPPER(d.format) = 'EPUB'
            JOIN books_authors_link bal ON bal.book = b.id
            JOIN authors a ON a.id = bal.author
            GROUP BY b.id
            ORDER BY b.timestamp DESC
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"Error reading Calibre database: {e}")
        return books

    for row in rows:
        epub_path = os.path.join(
            library_path, row["book_dir"], row["epub_stem"] + ".epub"
        )
        if not os.path.exists(epub_path):
            continue
        books.append(
            {
                "title": row["title"],
                "author": row["author"],
                "added_date": row["added_date"] or "1970-01-01T00:00:00+00:00",
                "epub_path": epub_path,
                "folder_path": os.path.join(library_path, row["book_dir"]),
            }
        )

    return books


# =============================================================================
# Ghost Folder Cleanup
# =============================================================================


def cleanup_ghost_folders(library_path: str) -> int:
    """Remove book folders that exist on disk but are not in Calibre's metadata.db.

    Returns the number of ghost folders removed.
    """
    db_path = os.path.join(library_path, "metadata.db")
    if not os.path.exists(db_path):
        return 0

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute("SELECT path FROM books").fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"Warning: Could not read database for cleanup: {e}")
        return 0

    db_book_dirs: set[str] = set()
    for (book_dir,) in rows:
        abs_path = os.path.normpath(os.path.join(library_path, book_dir))
        db_book_dirs.add(abs_path)

    db_author_dirs: set[str] = set()
    for d in db_book_dirs:
        db_author_dirs.add(os.path.dirname(d))

    ghost_folders: list[str] = []
    removed = 0

    for author_dir_name in os.listdir(library_path):
        author_path = os.path.join(library_path, author_dir_name)
        if not os.path.isdir(author_path) or author_dir_name.startswith("."):
            continue
        if author_dir_name in ("metadata.db", "metadata_db_prefs_backup.json"):
            continue

        for book_dir_name in os.listdir(author_path):
            book_path = os.path.normpath(os.path.join(author_path, book_dir_name))
            if not os.path.isdir(book_path):
                continue
            if book_path not in db_book_dirs:
                ghost_folders.append(book_path)

    if not ghost_folders:
        return 0

    print(f"\n{'=' * 60}")
    print(f"Found {len(ghost_folders)} ghost folder(s) not in Calibre database:")
    print(f"{'=' * 60}")
    for gf in ghost_folders:
        rel = os.path.relpath(gf, library_path)
        print(f"  - {rel}")

    print(f"\nThese folders exist on disk but are not registered in Calibre.")
    try:
        confirm = input("Delete them? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipping cleanup.")
        return 0

    if confirm != "y":
        print("Skipping cleanup.")
        return 0

    for gf in ghost_folders:
        try:
            shutil.rmtree(gf)
            removed += 1
            rel = os.path.relpath(gf, library_path)
            print(f"  Removed: {rel}")
        except OSError as e:
            print(f"  Failed to remove {gf}: {e}")

    for author_dir_name in os.listdir(library_path):
        author_path = os.path.join(library_path, author_dir_name)
        if not os.path.isdir(author_path) or author_dir_name.startswith("."):
            continue
        try:
            if not os.listdir(author_path):
                os.rmdir(author_path)
                print(f"  Removed empty author dir: {author_dir_name}")
        except OSError:
            pass

    print(f"\nCleaned up {removed} ghost folder(s).")
    return removed


# =============================================================================
# Interactive Library Browser
# =============================================================================


def display_library_browser(
    books: list[dict[str, str]], page_size: int = 20
) -> list[str] | None:
    """Display interactive paginated book list and let user select."""
    if not books:
        print("No books found in Calibre library.")
        return None

    all_books = books
    filtered_books = all_books
    search_query = ""

    prefs = _load_preferences()
    last_book_path = prefs.get("last_book_path", "")
    last_book_num = prefs.get("last_book_num", 0)

    current_page = 0
    if last_book_path:
        for i, book in enumerate(all_books):
            if book["epub_path"] == last_book_path:
                current_page = i // page_size
                last_book_num = i + 1
                break

    while True:
        total = len(filtered_books)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        current_page = max(0, min(current_page, total_pages - 1))

        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total)

        # Render page
        _render_page(
            filtered_books, start_idx, end_idx, current_page, total_pages,
            total, search_query, last_book_path,
        )

        # Get user input
        current_last_book_idx = _find_last_book_idx(filtered_books, last_book_path)
        hint = f" [Enter={current_last_book_idx + 1}]" if current_last_book_idx != -1 else ""
        print(
            f"Commands: [n]ext, [p]rev, [s]earch, [c]lear search, [q]uit, or # {hint}"
        )

        try:
            raw_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return None

        # Handle input
        result = _handle_browser_input(
            raw_input, filtered_books, all_books, current_page, total_pages,
            total, search_query, last_book_path, current_last_book_idx, prefs,
        )

        if result is None:
            # Quit
            return None
        elif isinstance(result, list):
            # Selection made
            return result
        else:
            # Updated state
            current_page, filtered_books, search_query = result


def _render_page(
    filtered_books: list[dict[str, str]],
    start_idx: int,
    end_idx: int,
    current_page: int,
    total_pages: int,
    total: int,
    search_query: str,
    last_book_path: str,
) -> None:
    """Render a single page of the library browser."""
    print(f"\n{'=' * 60}")
    header = (
        f"Calibre Library - Page {current_page + 1}/{total_pages} ({total} books)"
    )
    if search_query:
        header += f" | Filter: '{search_query}'"
    print(header)
    print(f"{'=' * 60}\n")

    if total == 0:
        print("  No books match your search.")
    else:
        for i in range(start_idx, end_idx):
            book = filtered_books[i]
            display_title = (
                book["title"][:42] + "..."
                if len(book["title"]) > 45
                else book["title"]
            )
            marker = " *" if last_book_path == book["epub_path"] else ""
            print(f"  [{i + 1:3d}] {display_title}{marker}")
            print(f"        by {book['author']}")

    print(f"\n{'─' * 60}")


def _find_last_book_idx(
    filtered_books: list[dict[str, str]], last_book_path: str
) -> int:
    """Find index of last selected book in filtered list."""
    if last_book_path:
        for i, b in enumerate(filtered_books):
            if b["epub_path"] == last_book_path:
                return i
    return -1


def _handle_browser_input(
    raw_input: str,
    filtered_books: list[dict[str, str]],
    all_books: list[dict[str, str]],
    current_page: int,
    total_pages: int,
    total: int,
    search_query: str,
    last_book_path: str,
    current_last_book_idx: int,
    prefs: dict[str, Any],
) -> list[str] | tuple[int, list[dict[str, str]], str] | None:
    """Handle user input in library browser.

    Returns:
        list[str]: Selected paths (done)
        tuple: (page, filtered_books, search_query) to continue browsing
        None: Quit
    """
    user_input = raw_input.lower()

    if user_input == "q":
        print("Cancelled.")
        return None
    elif user_input == "n":
        if current_page < total_pages - 1:
            current_page += 1
        else:
            print("Already at last page.")
        return (current_page, filtered_books, search_query)
    elif user_input == "p":
        if current_page > 0:
            current_page -= 1
        else:
            print("Already at first page.")
        return (current_page, filtered_books, search_query)
    elif user_input == "s":
        query = input("Enter search term (title or author): ").strip()
        if query:
            search_query = query
            filtered_books = [
                b
                for b in all_books
                if query.lower() in b["title"].lower()
                or query.lower() in b["author"].lower()
            ]
            current_page = 0
        else:
            print("Search cancelled.")
        return (current_page, filtered_books, search_query)
    elif user_input == "c":
        return (0, all_books, "")
    elif not user_input and current_last_book_idx != -1:
        selected = filtered_books[current_last_book_idx]
        print(f"\nSelected: {selected['title']} by {selected['author']}")
        return [selected["epub_path"]]
    else:
        return _handle_selection(
            raw_input, filtered_books, all_books, total,
            current_page, search_query, prefs,
        )


def _handle_selection(
    raw_input: str,
    filtered_books: list[dict[str, str]],
    all_books: list[dict[str, str]],
    total: int,
    current_page: int,
    search_query: str,
    prefs: dict[str, Any],
) -> list[str] | tuple[int, list[dict[str, str]], str]:
    """Handle numeric selection or implicit search."""
    try:
        is_selection = True
        cleaned = raw_input.replace("\uff0c", ",").replace(" ", ",")
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]

        indices = []
        for p in parts:
            if "-" in p:
                try:
                    s, e = map(int, p.split("-"))
                    if s > e:
                        s, e = e, s
                    indices.extend(range(s, e + 1))
                except ValueError:
                    is_selection = False
                    break
            else:
                try:
                    indices.append(int(p))
                except ValueError:
                    is_selection = False
                    break

        if not is_selection or not indices:
            raise ValueError("Not a selection")

        valid_indices = sorted(list(set([i for i in indices if 1 <= i <= total])))

        if not valid_indices:
            print(f"  [!] No valid book numbers in range (1-{total}).")
            return (current_page, filtered_books, search_query)

        print()
        selected_paths = []
        for idx in valid_indices:
            print(f"  [+] Selected: {filtered_books[idx - 1]['title']}")
            selected_paths.append(filtered_books[idx - 1]["epub_path"])

        if selected_paths:
            last_idx = valid_indices[-1]
            last_sel = filtered_books[last_idx - 1]
            prefs["last_book_path"] = last_sel["epub_path"]
            for global_idx, b in enumerate(all_books):
                if b["epub_path"] == last_sel["epub_path"]:
                    prefs["last_book_num"] = global_idx + 1
                    break
            _save_preferences(prefs)

            seen = set()
            unique_paths = []
            for p in selected_paths:
                if p not in seen:
                    seen.add(p)
                    unique_paths.append(p)
            return unique_paths

    except ValueError:
        # Implicit search
        search_query = raw_input
        print(f"Searching for: '{search_query}'")
        filtered_books = [
            b
            for b in all_books
            if search_query.lower() in b["title"].lower()
            or search_query.lower() in b["author"].lower()
        ]
        return (0, filtered_books, search_query)

    return (current_page, filtered_books, search_query)
